"""Single-job subprocess worker loop with durable supervision boundaries."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.jobs import JobArtifactRecord, JobRecord, JobStatus
from ansys_research_runner.services.job_registry import InvalidJobTransition, JobRegistry
from ansys_research_runner.services.process_supervisor import ProcessSupervisor


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class JobCommandFactory(Protocol):
    """Build a reviewed argv vector for one already validated job."""

    def build(self, job: JobRecord, job_dir: Path) -> Sequence[str]:
        """Return an executable and arguments without invoking a shell."""
        ...


class SubprocessJobWorker:
    """Claim and supervise one job while keeping the caller process alive on faults."""

    def __init__(
        self,
        registry: JobRegistry,
        command_factory: JobCommandFactory,
        *,
        runs_root: Path | None = None,
        poll_interval_s: float = 0.02,
        lease_seconds: float = 5.0,
    ) -> None:
        self._registry = registry
        self._factory = command_factory
        self._runs_root = (runs_root or RunnerPaths.from_environment().runs).resolve()
        self._runs_root.mkdir(parents=True, exist_ok=True)
        self._poll_interval_s = poll_interval_s
        self._lease_seconds = lease_seconds
        self._supervisor = ProcessSupervisor(registry)

    def run_once(self, worker_id: str) -> JobRecord | None:
        """Claim at most one job and run it to a durable terminal state."""

        job = self._registry.claim_next(worker_id, lease_seconds=self._lease_seconds)
        if job is None:
            return None
        job_dir = (self._runs_root / job.job_id).resolve()
        if not job_dir.is_relative_to(self._runs_root):
            return self._registry.transition(
                job.job_id,
                JobStatus.FAILED_INPUT,
                event_type="PATH_POLICY_REJECTED",
                error_code="PATH_OUTSIDE_RUNTIME",
                error_message="Resolved job path escaped the configured runs root.",
                worker_id=worker_id,
            )
        try:
            command = [str(value) for value in self._factory.build(job, job_dir)]
            if not command or not command[0]:
                raise ValueError("Worker command factory returned an empty executable.")
        except Exception as exc:  # noqa: BLE001 - factory failures become structured job errors
            return self._registry.transition(
                job.job_id,
                JobStatus.FAILED_INPUT,
                event_type="WORKER_COMMAND_REJECTED",
                error_code="WORKER_COMMAND_INVALID",
                error_message=str(exc),
                worker_id=worker_id,
            )

        self._registry.transition(job.job_id, JobStatus.STAGING, worker_id=worker_id)
        for directory in (job_dir / "control", job_dir / "artifacts", job_dir / "logs"):
            directory.mkdir(parents=True, exist_ok=True)
        self._registry.transition(job.job_id, JobStatus.WAITING_RESOURCE, worker_id=worker_id)
        self._registry.transition(job.job_id, JobStatus.LAUNCHING, worker_id=worker_id)

        stdout_stream = (job_dir / "logs" / "worker.stdout.log").open(
            "w", encoding="utf-8", newline="\n"
        )
        stderr_stream = (job_dir / "logs" / "worker.stderr.log").open(
            "w", encoding="utf-8", newline="\n"
        )
        try:
            try:
                process = subprocess.Popen(  # noqa: S603 - reviewed argv comes from an adapter
                    command,
                    cwd=job_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                    text=True,
                    shell=False,
                )
            except OSError as exc:
                return self._launch_failure(job.job_id, worker_id, exc, job_dir)
            try:
                owned = self._supervisor.register_spawned_process(
                    job.job_id, process.pid, launcher_pid=process.pid
                )
            except Exception as exc:  # noqa: BLE001 - fast child exit is a launch failure
                with suppress(OSError):
                    process.kill()
                return self._launch_failure(job.job_id, worker_id, exc, job_dir)
            self._registry.transition(job.job_id, JobStatus.PRECHECKING, worker_id=worker_id)
            terminal = self._monitor(
                job=self._registry.get_job(job.job_id),
                worker_id=worker_id,
                job_dir=job_dir,
                process=process,
            )
            if process.poll() is not None:
                current_owned = next(
                    (
                        record
                        for record in self._registry.list_owned_processes(job.job_id)
                        if record.pid == owned.pid and record.create_time == owned.create_time
                    ),
                    None,
                )
                if current_owned is not None and current_owned.ended_at is None:
                    self._registry.mark_process_ended(
                        job.job_id,
                        owned.pid,
                        owned.create_time,
                        result=f"natural_exit:{process.returncode}",
                    )
            return terminal
        finally:
            stdout_stream.close()
            stderr_stream.close()

    def _monitor(
        self,
        *,
        job: JobRecord,
        worker_id: str,
        job_dir: Path,
        process: subprocess.Popen[str],
    ) -> JobRecord:
        started = time.monotonic()
        processed_events = 0
        last_heartbeat_value: float | None = None
        peak_rss = 0
        peak_cpu = 0.0
        peak_disk = 0
        while True:
            processed_events = self._apply_worker_events(
                job.job_id, worker_id, job_dir, processed_events
            )
            current = self._registry.get_job(job.job_id)
            if current.status is JobStatus.CANCEL_REQUESTED:
                cleanup = self._supervisor.terminate_owned(job.job_id)
                self._register_artifacts(job.job_id, job_dir)
                return self._registry.transition(
                    job.job_id,
                    JobStatus.CANCELLED,
                    event_type="CANCELLATION_COMPLETED",
                    details={"cleanup_remaining": list(cleanup.remaining)},
                    worker_id=worker_id,
                )

            snapshot = self._supervisor.resource_snapshot(job.job_id, job_dir)
            peak_rss = max(peak_rss, snapshot.rss_bytes)
            peak_cpu = max(peak_cpu, snapshot.cpu_time_s)
            peak_disk = max(peak_disk, snapshot.workdir_bytes)
            elapsed = time.monotonic() - started
            heartbeat_value = self._heartbeat_value(job_dir)
            if heartbeat_value is not None and heartbeat_value != last_heartbeat_value:
                last_heartbeat_value = heartbeat_value
                self._registry.heartbeat(job.job_id, worker_id, lease_seconds=self._lease_seconds)

            if elapsed > job.wall_clock_timeout_s:
                return self._timeout_failure(
                    job.job_id,
                    worker_id,
                    job_dir,
                    "WALL_CLOCK_TIMEOUT",
                    f"Wall-clock limit of {job.wall_clock_timeout_s}s exceeded.",
                )
            if last_heartbeat_value is None and elapsed > job.launch_timeout_s:
                return self._timeout_failure(
                    job.job_id,
                    worker_id,
                    job_dir,
                    "LAUNCH_TIMEOUT",
                    f"No first heartbeat within {job.launch_timeout_s}s.",
                    target=JobStatus.FAILED_LAUNCH,
                )
            if (
                last_heartbeat_value is not None
                and time.time() - last_heartbeat_value > job.heartbeat_timeout_s
            ):
                return self._timeout_failure(
                    job.job_id,
                    worker_id,
                    job_dir,
                    "HEARTBEAT_TIMEOUT",
                    f"Heartbeat age exceeded {job.heartbeat_timeout_s}s.",
                )

            return_code = process.poll()
            if return_code is not None:
                processed_events = self._apply_worker_events(
                    job.job_id, worker_id, job_dir, processed_events
                )
                self._mark_natural_exit(job.job_id, process)
                self._register_artifacts(job.job_id, job_dir)
                current = self._registry.get_job(job.job_id)
                resources = {
                    "peak_rss_bytes": peak_rss,
                    "peak_cpu_time_s": peak_cpu,
                    "peak_workdir_bytes": peak_disk,
                    "worker_return_code": return_code,
                }
                if return_code == 0 and current.status is JobStatus.EXPORTING:
                    return self._registry.transition(
                        job.job_id,
                        JobStatus.SUCCEEDED,
                        event_type="WORKER_SUCCEEDED",
                        result=resources,
                        worker_id=worker_id,
                    )
                target = self._failure_status(current.status)
                return self._registry.transition(
                    job.job_id,
                    target,
                    event_type="WORKER_EXITED",
                    details=resources,
                    error_code="WORKER_EXIT",
                    error_message=f"Worker exited with return code {return_code}.",
                    worker_id=worker_id,
                )
            time.sleep(self._poll_interval_s)

    def _mark_natural_exit(self, job_id: str, process: subprocess.Popen[str]) -> None:
        """Record worker exit before exposing a terminal job state to pollers."""

        current_owned = next(
            (
                record
                for record in self._registry.list_owned_processes(job_id)
                if record.pid == process.pid and record.ended_at is None
            ),
            None,
        )
        if current_owned is not None:
            self._registry.mark_process_ended(
                job_id,
                current_owned.pid,
                current_owned.create_time,
                result=f"natural_exit:{process.returncode}",
            )

    def _apply_worker_events(
        self, job_id: str, worker_id: str, job_dir: Path, processed: int
    ) -> int:
        event_path = job_dir / "control" / "events.jsonl"
        if not event_path.is_file():
            return processed
        lines = event_path.read_text(encoding="utf-8").splitlines()
        for line in lines[processed:]:
            payload = json.loads(line)
            if payload.get("type") != "phase":
                continue
            target = JobStatus(str(payload["status"]))
            current = self._registry.get_job(job_id)
            if target is current.status:
                continue
            self._registry.transition(
                job_id,
                target,
                event_type="WORKER_PHASE",
                message=str(payload.get("detail", "")) or None,
                worker_id=worker_id,
            )
        return len(lines)

    @staticmethod
    def _heartbeat_value(job_dir: Path) -> float | None:
        path = job_dir / "control" / "heartbeat.txt"
        try:
            return float(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _timeout_failure(
        self,
        job_id: str,
        worker_id: str,
        job_dir: Path,
        code: str,
        message: str,
        *,
        target: JobStatus = JobStatus.FAILED_RESOURCE,
    ) -> JobRecord:
        cleanup = self._supervisor.terminate_owned(job_id)
        self._register_artifacts(job_id, job_dir)
        return self._registry.transition(
            job_id,
            target,
            event_type=code,
            details={"cleanup_remaining": list(cleanup.remaining)},
            error_code=code,
            error_message=message,
            worker_id=worker_id,
        )

    def _launch_failure(
        self, job_id: str, worker_id: str, error: Exception, job_dir: Path
    ) -> JobRecord:
        self._register_artifacts(job_id, job_dir)
        return self._registry.transition(
            job_id,
            JobStatus.FAILED_LAUNCH,
            event_type="WORKER_LAUNCH_FAILED",
            error_code="WORKER_LAUNCH_FAILED",
            error_message=str(error),
            worker_id=worker_id,
        )

    def _register_artifacts(self, job_id: str, job_dir: Path) -> None:
        media_types = {
            ".json": "application/json",
            ".txt": "text/plain",
            ".log": "text/plain",
            ".csv": "text/csv",
            ".h5": "application/x-hdf5",
            ".png": "image/png",
        }
        for root, kind in ((job_dir / "artifacts", "artifact"), (job_dir / "logs", "log")):
            if not root.is_dir():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                resolved = path.resolve()
                if not resolved.is_relative_to(job_dir):
                    continue
                relative = resolved.relative_to(job_dir).as_posix()
                payload = resolved.read_bytes()
                self._registry.register_artifact(
                    JobArtifactRecord(
                        job_id=job_id,
                        path=relative,
                        kind=kind,
                        media_type=media_types.get(
                            resolved.suffix.lower(), "application/octet-stream"
                        ),
                        size_bytes=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest(),
                        created_at=_utc_now(),
                    )
                )

    @staticmethod
    def _failure_status(current: JobStatus) -> JobStatus:
        mapping: Mapping[JobStatus, JobStatus] = {
            JobStatus.VALIDATING: JobStatus.FAILED_INPUT,
            JobStatus.STAGING: JobStatus.FAILED_INPUT,
            JobStatus.WAITING_RESOURCE: JobStatus.FAILED_RESOURCE,
            JobStatus.LAUNCHING: JobStatus.FAILED_LAUNCH,
            JobStatus.PRECHECKING: JobStatus.FAILED_PRECHECK,
            JobStatus.SOLVING: JobStatus.FAILED_SOLVER,
            JobStatus.POSTPROCESSING: JobStatus.FAILED_POSTPROCESS,
            JobStatus.EXPORTING: JobStatus.FAILED_EXPORT,
        }
        try:
            return mapping[current]
        except KeyError as exc:
            raise InvalidJobTransition(
                f"Cannot classify worker exit from state {current.value}."
            ) from exc
