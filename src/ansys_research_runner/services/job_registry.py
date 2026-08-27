"""Short-transaction SQLite WAL registry for durable single-worker jobs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.jobs import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobArtifactRecord,
    JobEvent,
    JobRecord,
    JobStatus,
    OwnedProcessRecord,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


FORWARD_PHASES: Final[tuple[JobStatus, ...]] = (
    JobStatus.QUEUED,
    JobStatus.VALIDATING,
    JobStatus.STAGING,
    JobStatus.WAITING_RESOURCE,
    JobStatus.LAUNCHING,
    JobStatus.PRECHECKING,
    JobStatus.SOLVING,
    JobStatus.POSTPROCESSING,
    JobStatus.EXPORTING,
    JobStatus.SUCCEEDED,
)


def _transition_graph() -> dict[JobStatus, frozenset[JobStatus]]:
    graph: dict[JobStatus, set[JobStatus]] = {status: set() for status in JobStatus}
    for current, following in pairwise(FORWARD_PHASES):
        graph[current].add(following)
    graph[JobStatus.VALIDATING].add(JobStatus.FAILED_INPUT)
    graph[JobStatus.STAGING].add(JobStatus.FAILED_INPUT)
    graph[JobStatus.WAITING_RESOURCE].add(JobStatus.FAILED_RESOURCE)
    graph[JobStatus.LAUNCHING].update(
        {JobStatus.FAILED_LAUNCH, JobStatus.FAILED_LICENSE, JobStatus.FAILED_RESOURCE}
    )
    graph[JobStatus.PRECHECKING].update(
        {
            JobStatus.FAILED_LAUNCH,
            JobStatus.FAILED_PRECHECK,
            JobStatus.FAILED_LICENSE,
            JobStatus.FAILED_RESOURCE,
        }
    )
    graph[JobStatus.SOLVING].update(
        {JobStatus.FAILED_SOLVER, JobStatus.FAILED_LICENSE, JobStatus.FAILED_RESOURCE}
    )
    graph[JobStatus.POSTPROCESSING].update(
        {JobStatus.FAILED_POSTPROCESS, JobStatus.FAILED_RESOURCE}
    )
    graph[JobStatus.EXPORTING].update({JobStatus.FAILED_EXPORT, JobStatus.FAILED_RESOURCE})
    for status in {JobStatus.QUEUED, *ACTIVE_JOB_STATUSES}:
        graph[status].update({JobStatus.CANCEL_REQUESTED, JobStatus.RECOVERY_REQUIRED})
    graph[JobStatus.CANCEL_REQUESTED].update(
        {JobStatus.CANCELLED, JobStatus.FAILED_RESOURCE, JobStatus.RECOVERY_REQUIRED}
    )
    graph[JobStatus.RECOVERY_REQUIRED].update({JobStatus.QUEUED, JobStatus.CANCELLED})
    return {status: frozenset(targets) for status, targets in graph.items()}


ALLOWED_TRANSITIONS: Final = _transition_graph()


class InvalidJobTransition(ValueError):
    """Raised before any write when a requested state edge is forbidden."""


class JobNotFoundError(KeyError):
    """Raised when a requested durable job does not exist."""


class JobRegistry:
    """Durable registry using WAL, conditional claims, and append-only audit events."""

    def __init__(self, path: Path | None = None, *, busy_timeout_ms: int = 5000) -> None:
        self.path = (path or RunnerPaths.from_environment().database).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    worker_id TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    result_json TEXT,
                    launch_timeout_s REAL NOT NULL,
                    heartbeat_timeout_s REAL NOT NULL,
                    wall_clock_timeout_s REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_claim
                    ON jobs(status, created_at, job_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_lease
                    ON jobs(status, lease_expires_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    sequence INTEGER NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS owned_processes (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    pid INTEGER NOT NULL,
                    parent_pid INTEGER,
                    create_time REAL NOT NULL,
                    executable_path TEXT NOT NULL,
                    command_line_fingerprint TEXT NOT NULL,
                    launcher_pid INTEGER NOT NULL,
                    discovery_method TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    ended_at TEXT,
                    termination_result TEXT,
                    PRIMARY KEY(job_id, pid, create_time)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    path TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, path)
                );
                PRAGMA user_version=1;
                """
            )

    def pragmas(self) -> dict[str, int | str]:
        """Return authoritative durability/locking settings for Gate evidence."""

        with self._connection() as connection:
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            busy = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        return {"journal_mode": journal, "busy_timeout": busy, "foreign_keys": foreign_keys}

    def create_job(
        self,
        job_id: str,
        *,
        kind: str,
        request: Mapping[str, Any],
        launch_timeout_s: float = 30.0,
        heartbeat_timeout_s: float = 30.0,
        wall_clock_timeout_s: float = 3600.0,
        now: datetime | None = None,
    ) -> JobRecord:
        """Create a queued job and its initial audit event atomically."""

        created = _iso(now or _utc_now())
        candidate = JobRecord(
            job_id=job_id,
            kind=kind,
            request=dict(request),
            status=JobStatus.QUEUED,
            created_at=created,
            updated_at=created,
            launch_timeout_s=launch_timeout_s,
            heartbeat_timeout_s=heartbeat_timeout_s,
            wall_clock_timeout_s=wall_clock_timeout_s,
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO jobs(
                        job_id, kind, request_json, status, created_at, updated_at,
                        launch_timeout_s, heartbeat_timeout_s, wall_clock_timeout_s
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.job_id,
                        candidate.kind,
                        _json(candidate.request),
                        candidate.status.value,
                        created,
                        created,
                        launch_timeout_s,
                        heartbeat_timeout_s,
                        wall_clock_timeout_s,
                    ),
                )
                self._insert_event(
                    connection,
                    job_id,
                    None,
                    JobStatus.QUEUED,
                    event_type="JOB_CREATED",
                    message=None,
                    details={},
                    created_at=created,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return candidate

    def get_job(self, job_id: str) -> JobRecord:
        """Load the current snapshot for one job."""

        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._job_from_row(row)

    def list_jobs(self) -> tuple[JobRecord, ...]:
        """List jobs in deterministic creation order."""

        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created_at, job_id").fetchall()
        return tuple(self._job_from_row(row) for row in rows)

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> JobRecord | None:
        """Atomically claim at most one queued job using a conditional update."""

        claimed_at = now or _utc_now()
        timestamp = _iso(claimed_at)
        lease = _iso(claimed_at + timedelta(seconds=lease_seconds))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT job_id FROM jobs
                    WHERE status=? ORDER BY created_at, job_id LIMIT 1
                    """,
                    (JobStatus.QUEUED.value,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                job_id = str(row["job_id"])
                updated = connection.execute(
                    """
                    UPDATE jobs
                    SET status=?, updated_at=?, worker_id=?, lease_expires_at=?,
                        heartbeat_at=?, attempt=attempt+1
                    WHERE job_id=? AND status=?
                    """,
                    (
                        JobStatus.VALIDATING.value,
                        timestamp,
                        worker_id,
                        lease,
                        timestamp,
                        job_id,
                        JobStatus.QUEUED.value,
                    ),
                ).rowcount
                if updated != 1:
                    connection.rollback()
                    return None
                self._insert_event(
                    connection,
                    job_id,
                    JobStatus.QUEUED,
                    JobStatus.VALIDATING,
                    event_type="JOB_CLAIMED",
                    message=None,
                    details={"worker_id": worker_id, "lease_expires_at": lease},
                    created_at=timestamp,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_job(job_id)

    def transition(
        self,
        job_id: str,
        to_status: JobStatus,
        *,
        event_type: str = "STATE_TRANSITION",
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result: Mapping[str, Any] | None = None,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> JobRecord:
        """Apply one legal state edge and append its audit record in one transaction."""

        timestamp = _iso(now or _utc_now())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, worker_id FROM jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    raise JobNotFoundError(job_id)
                current = JobStatus(str(row["status"]))
                if worker_id is not None and row["worker_id"] != worker_id:
                    raise InvalidJobTransition(f"Worker {worker_id!r} does not own job {job_id!r}.")
                if to_status not in ALLOWED_TRANSITIONS[current]:
                    raise InvalidJobTransition(
                        f"Forbidden job transition: {current.value} -> {to_status.value}"
                    )
                terminal = to_status in TERMINAL_JOB_STATUSES
                connection.execute(
                    """
                    UPDATE jobs SET status=?, updated_at=?, error_code=?, error_message=?,
                        result_json=?, worker_id=CASE WHEN ? THEN NULL ELSE worker_id END,
                        lease_expires_at=CASE WHEN ? THEN NULL ELSE lease_expires_at END
                    WHERE job_id=?
                    """,
                    (
                        to_status.value,
                        timestamp,
                        error_code,
                        error_message,
                        None if result is None else _json(dict(result)),
                        terminal,
                        terminal,
                        job_id,
                    ),
                )
                self._insert_event(
                    connection,
                    job_id,
                    current,
                    to_status,
                    event_type=event_type,
                    message=message,
                    details=dict(details or {}),
                    created_at=timestamp,
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_job(job_id)

    def heartbeat(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        """Renew a live worker lease without holding a long transaction."""

        heartbeat = now or _utc_now()
        timestamp = _iso(heartbeat)
        lease = _iso(heartbeat + timedelta(seconds=lease_seconds))
        active_values = tuple(status.value for status in ACTIVE_JOB_STATUSES)
        placeholders = ",".join("?" for _ in active_values)
        with self._connection() as connection:
            updated = connection.execute(
                f"""
                UPDATE jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=?
                WHERE job_id=? AND worker_id=? AND status IN ({placeholders})
                """,
                (timestamp, lease, timestamp, job_id, worker_id, *active_values),
            ).rowcount
        return updated == 1

    def request_cancel(self, job_id: str, *, message: str | None = None) -> JobRecord:
        """Durably request cancellation without killing an unowned process."""

        current = self.get_job(job_id)
        if current.status in TERMINAL_JOB_STATUSES or current.status is JobStatus.RECOVERY_REQUIRED:
            return current
        requested = self.transition(
            job_id,
            JobStatus.CANCEL_REQUESTED,
            event_type="CANCEL_REQUESTED",
            message=message,
        )
        if current.status is JobStatus.QUEUED:
            return self.transition(
                job_id,
                JobStatus.CANCELLED,
                event_type="QUEUED_JOB_CANCELLED",
                message="Queued job cancelled without launching a worker.",
            )
        return requested

    def recover_stale(
        self, *, now: datetime | None = None, heartbeat_grace_s: float = 0.0
    ) -> tuple[str, ...]:
        """Move expired leased jobs to RECOVERY_REQUIRED with prior-state evidence."""

        observed = now or _utc_now()
        cutoff = _iso(observed - timedelta(seconds=heartbeat_grace_s))
        timestamp = _iso(observed)
        recoverable_values = tuple(status.value for status in ACTIVE_JOB_STATUSES)
        placeholders = ",".join("?" for _ in recoverable_values)
        recovered: list[str] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT job_id, status, worker_id, lease_expires_at, heartbeat_at
                    FROM jobs
                    WHERE status IN ({placeholders})
                      AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
                      AND (heartbeat_at IS NULL OR heartbeat_at <= ?)
                    ORDER BY created_at, job_id
                    """,
                    (*recoverable_values, timestamp, cutoff),
                ).fetchall()
                for row in rows:
                    job_id = str(row["job_id"])
                    prior = JobStatus(str(row["status"]))
                    connection.execute(
                        """
                        UPDATE jobs SET status=?, updated_at=?, worker_id=NULL,
                            lease_expires_at=NULL
                        WHERE job_id=? AND status=?
                        """,
                        (JobStatus.RECOVERY_REQUIRED.value, timestamp, job_id, prior.value),
                    )
                    self._insert_event(
                        connection,
                        job_id,
                        prior,
                        JobStatus.RECOVERY_REQUIRED,
                        event_type="STALE_LEASE_RECOVERED",
                        message="Worker lease expired before a terminal state was recorded.",
                        details={
                            "prior_status": prior.value,
                            "prior_worker_id": row["worker_id"],
                            "lease_expires_at": row["lease_expires_at"],
                            "heartbeat_at": row["heartbeat_at"],
                        },
                        created_at=timestamp,
                    )
                    recovered.append(job_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return tuple(recovered)

    def requeue_recoverable(self, job_id: str) -> JobRecord:
        """Requeue only a stale job that had not entered the solve phase."""

        events = self.list_events(job_id)
        if not events or events[-1].to_status is not JobStatus.RECOVERY_REQUIRED:
            raise InvalidJobTransition("Job is not awaiting recovery.")
        prior_raw = events[-1].details.get("prior_status")
        safe_prior = {
            JobStatus.VALIDATING,
            JobStatus.STAGING,
            JobStatus.WAITING_RESOURCE,
            JobStatus.LAUNCHING,
            JobStatus.PRECHECKING,
        }
        if prior_raw is None or JobStatus(str(prior_raw)) not in safe_prior:
            raise InvalidJobTransition(
                "Job crossed the solve boundary and cannot be auto-requeued."
            )
        return self.transition(
            job_id,
            JobStatus.QUEUED,
            event_type="RECOVERY_REQUEUED",
            message="Pre-solve stale job safely returned to the queue.",
        )

    def list_events(self, job_id: str) -> tuple[JobEvent, ...]:
        """Return the immutable audit trail for one job."""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id=? ORDER BY sequence", (job_id,)
            ).fetchall()
        return tuple(self._event_from_row(row) for row in rows)

    def register_process(self, record: OwnedProcessRecord) -> None:
        """Persist an exact owned-process identity; never infer ownership from name alone."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO owned_processes(
                    job_id, pid, parent_pid, create_time, executable_path,
                    command_line_fingerprint, launcher_pid, discovery_method,
                    registered_at, ended_at, termination_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_id,
                    record.pid,
                    record.parent_pid,
                    record.create_time,
                    record.executable_path,
                    record.command_line_fingerprint,
                    record.launcher_pid,
                    record.discovery_method,
                    record.registered_at,
                    record.ended_at,
                    record.termination_result,
                ),
            )

    def mark_process_ended(
        self,
        job_id: str,
        pid: int,
        create_time: float,
        *,
        result: str,
        now: datetime | None = None,
    ) -> None:
        """Retain process ownership evidence while recording its terminal observation."""

        with self._connection() as connection:
            connection.execute(
                """
                UPDATE owned_processes SET ended_at=?, termination_result=?
                WHERE job_id=? AND pid=? AND create_time=?
                """,
                (_iso(now or _utc_now()), result, job_id, pid, create_time),
            )

    def list_owned_processes(
        self, job_id: str, *, include_ended: bool = True
    ) -> tuple[OwnedProcessRecord, ...]:
        """Return exact process identities registered for one job."""

        query = "SELECT * FROM owned_processes WHERE job_id=?"
        if not include_ended:
            query += " AND ended_at IS NULL"
        query += " ORDER BY registered_at, pid"
        with self._connection() as connection:
            rows = connection.execute(query, (job_id,)).fetchall()
        return tuple(self._process_from_row(row) for row in rows)

    def register_artifact(self, record: JobArtifactRecord) -> None:
        """Upsert immutable metadata for an artifact observed on disk."""

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO artifacts(
                    job_id, path, kind, media_type, size_bytes, sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, path) DO UPDATE SET
                    kind=excluded.kind,
                    media_type=excluded.media_type,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    created_at=excluded.created_at
                """,
                (
                    record.job_id,
                    record.path,
                    record.kind,
                    record.media_type,
                    record.size_bytes,
                    record.sha256,
                    record.created_at,
                ),
            )

    def list_artifacts(self, job_id: str) -> tuple[JobArtifactRecord, ...]:
        """Return artifact metadata in path order."""

        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY path", (job_id,)
            ).fetchall()
        return tuple(self._artifact_from_row(row) for row in rows)

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        from_status: JobStatus | None,
        to_status: JobStatus,
        *,
        event_type: str,
        message: str | None,
        details: Mapping[str, Any],
        created_at: str,
    ) -> None:
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM job_events WHERE job_id=?",
                (job_id,),
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO job_events(
                job_id, sequence, from_status, to_status, event_type,
                message, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                sequence,
                None if from_status is None else from_status.value,
                to_status.value,
                event_type,
                message,
                _json(dict(details)),
                created_at,
            ),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            job_id=row["job_id"],
            kind=row["kind"],
            request=json.loads(row["request_json"]),
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"],
            heartbeat_at=row["heartbeat_at"],
            attempt=row["attempt"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            result=None if row["result_json"] is None else json.loads(row["result_json"]),
            launch_timeout_s=row["launch_timeout_s"],
            heartbeat_timeout_s=row["heartbeat_timeout_s"],
            wall_clock_timeout_s=row["wall_clock_timeout_s"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> JobEvent:
        return JobEvent(
            event_id=row["event_id"],
            job_id=row["job_id"],
            sequence=row["sequence"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            event_type=row["event_type"],
            message=row["message"],
            details=json.loads(row["details_json"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _process_from_row(row: sqlite3.Row) -> OwnedProcessRecord:
        return OwnedProcessRecord(
            job_id=row["job_id"],
            pid=row["pid"],
            parent_pid=row["parent_pid"],
            create_time=row["create_time"],
            executable_path=row["executable_path"],
            command_line_fingerprint=row["command_line_fingerprint"],
            launcher_pid=row["launcher_pid"],
            discovery_method=row["discovery_method"],
            registered_at=row["registered_at"],
            ended_at=row["ended_at"],
            termination_result=row["termination_result"],
        )

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> JobArtifactRecord:
        return JobArtifactRecord(
            job_id=row["job_id"],
            path=row["path"],
            kind=row["kind"],
            media_type=row["media_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=row["created_at"],
        )
