"""G6 durable registry, transition, claim, and recovery tests."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ansys_research_runner.domain.jobs import JobArtifactRecord, JobStatus
from ansys_research_runner.services.job_registry import (
    InvalidJobTransition,
    JobRegistry,
)

NOW = datetime(2026, 8, 23, 8, 0, tzinfo=UTC)


def _registry(tmp_path, *, timeout: int = 5000) -> JobRegistry:
    return JobRegistry(tmp_path / "jobs.sqlite", busy_timeout_ms=timeout)


def test_registry_enables_wal_busy_timeout_and_foreign_keys(tmp_path) -> None:
    registry = _registry(tmp_path)

    assert registry.pragmas() == {
        "journal_mode": "wal",
        "busy_timeout": 5000,
        "foreign_keys": 1,
    }


def test_create_claim_and_full_transition_audit(tmp_path) -> None:
    registry = _registry(tmp_path)
    created = registry.create_job("job-1", kind="thermal", request={"case": "box"}, now=NOW)

    assert created.status is JobStatus.QUEUED
    claimed = registry.claim_next("worker-a", lease_seconds=5, now=NOW)
    assert claimed is not None
    assert claimed.status is JobStatus.VALIDATING
    assert claimed.attempt == 1
    for status in (
        JobStatus.STAGING,
        JobStatus.WAITING_RESOURCE,
        JobStatus.LAUNCHING,
        JobStatus.PRECHECKING,
        JobStatus.SOLVING,
        JobStatus.POSTPROCESSING,
        JobStatus.EXPORTING,
        JobStatus.SUCCEEDED,
    ):
        registry.transition("job-1", status, worker_id="worker-a")

    events = registry.list_events("job-1")
    assert [event.sequence for event in events] == list(range(1, 11))
    assert [event.to_status for event in events] == [
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
    ]
    assert registry.get_job("job-1").worker_id is None


def test_forbidden_direct_queue_to_solving_is_rejected_without_event(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.create_job("bad-edge", kind="thermal", request={})

    with pytest.raises(InvalidJobTransition, match="QUEUED -> SOLVING"):
        registry.transition("bad-edge", JobStatus.SOLVING)

    assert registry.get_job("bad-edge").status is JobStatus.QUEUED
    assert len(registry.list_events("bad-edge")) == 1


def test_two_workers_cannot_claim_the_same_job(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite"
    JobRegistry(database).create_job("one", kind="thermal", request={})

    def claim(worker_id: str):
        return JobRegistry(database).claim_next(worker_id, lease_seconds=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].job_id == "one"
    assert JobRegistry(database).get_job("one").attempt == 1


def test_sqlite_locked_error_is_reproduced_and_not_hidden(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite"
    registry = JobRegistry(database, busy_timeout_ms=25)
    lock = sqlite3.connect(database, isolation_level=None)
    lock.execute("PRAGMA busy_timeout=0")
    lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            registry.create_job("locked", kind="thermal", request={})
    finally:
        lock.rollback()
        lock.close()


def test_stale_pre_solve_job_is_recovered_and_requeued_after_restart(tmp_path) -> None:
    database = tmp_path / "jobs.sqlite"
    first = JobRegistry(database)
    first.create_job("stale", kind="thermal", request={}, now=NOW)
    assert first.claim_next("dead-worker", lease_seconds=1, now=NOW) is not None
    first.transition("stale", JobStatus.STAGING, worker_id="dead-worker", now=NOW)

    restarted = JobRegistry(database)
    assert restarted.recover_stale(now=NOW + timedelta(seconds=2)) == ("stale",)
    assert restarted.get_job("stale").status is JobStatus.RECOVERY_REQUIRED
    restarted.requeue_recoverable("stale")
    claimed = restarted.claim_next(
        "replacement-worker", lease_seconds=5, now=NOW + timedelta(seconds=3)
    )

    assert claimed is not None
    assert claimed.attempt == 2
    assert [event.event_type for event in restarted.list_events("stale")][-3:] == [
        "STALE_LEASE_RECOVERED",
        "RECOVERY_REQUEUED",
        "JOB_CLAIMED",
    ]


def test_stale_solve_job_requires_manual_recovery_decision(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.create_job("solving", kind="thermal", request={}, now=NOW)
    registry.claim_next("dead-worker", lease_seconds=1, now=NOW)
    for status in (
        JobStatus.STAGING,
        JobStatus.WAITING_RESOURCE,
        JobStatus.LAUNCHING,
        JobStatus.PRECHECKING,
        JobStatus.SOLVING,
    ):
        registry.transition("solving", status, worker_id="dead-worker", now=NOW)
    registry.recover_stale(now=NOW + timedelta(seconds=2))

    with pytest.raises(InvalidJobTransition, match="solve boundary"):
        registry.requeue_recoverable("solving")


def test_queued_cancellation_never_launches_and_has_two_audit_edges(tmp_path) -> None:
    registry = _registry(tmp_path)
    registry.create_job("cancelled", kind="thermal", request={})

    result = registry.request_cancel("cancelled", message="user request")

    assert result.status is JobStatus.CANCELLED
    assert [event.to_status for event in registry.list_events("cancelled")] == [
        JobStatus.QUEUED,
        JobStatus.CANCEL_REQUESTED,
        JobStatus.CANCELLED,
    ]
    assert registry.claim_next("worker", lease_seconds=5) is None


def test_artifact_contract_rejects_escape_paths() -> None:
    common = {
        "job_id": "job",
        "kind": "artifact",
        "media_type": "text/plain",
        "size_bytes": 1,
        "sha256": "0" * 64,
        "created_at": "2026-08-23T08:00:00Z",
    }

    with pytest.raises(ValidationError, match="confined relative"):
        JobArtifactRecord(path="../outside.txt", **common)
    with pytest.raises(ValidationError, match="confined relative"):
        JobArtifactRecord(path="C:/outside.txt", **common)
