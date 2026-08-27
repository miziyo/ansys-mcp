"""G6 fault injection against real SQLite, filesystem, and worker subprocesses."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import psutil

from ansys_research_runner.domain.jobs import JobRecord, JobStatus, OwnedProcessRecord
from ansys_research_runner.services.job_registry import JobRegistry
from ansys_research_runner.services.job_worker_service import SubprocessJobWorker
from ansys_research_runner.services.process_supervisor import ProcessSupervisor


@dataclass(frozen=True, slots=True)
class DummyFactory:
    behavior: str
    duration_s: float = 0.2
    interval_s: float = 0.02

    def build(self, job: JobRecord, job_dir: Path) -> list[str]:
        del job
        return [
            sys.executable,
            "-m",
            "ansys_research_runner.adapters.worker.dummy_worker",
            "--job-dir",
            str(job_dir),
            "--behavior",
            self.behavior,
            "--duration-s",
            str(self.duration_s),
            "--interval-s",
            str(self.interval_s),
        ]


@dataclass(frozen=True, slots=True)
class MissingExecutableFactory:
    executable: Path

    def build(self, job: JobRecord, job_dir: Path) -> list[str]:
        del job, job_dir
        return [str(self.executable)]


def _setup(
    tmp_path: Path,
    behavior: str,
    *,
    launch_timeout_s: float = 1.5,
    heartbeat_timeout_s: float = 0.5,
    wall_clock_timeout_s: float = 2.0,
    duration_s: float = 0.2,
) -> tuple[JobRegistry, SubprocessJobWorker]:
    registry = JobRegistry(tmp_path / "jobs.sqlite")
    registry.create_job(
        f"job-{behavior}",
        kind="g6-fault-injection",
        request={"fixture": behavior},
        launch_timeout_s=launch_timeout_s,
        heartbeat_timeout_s=heartbeat_timeout_s,
        wall_clock_timeout_s=wall_clock_timeout_s,
    )
    worker = SubprocessJobWorker(
        registry,
        DummyFactory(behavior=behavior, duration_s=duration_s),
        runs_root=tmp_path / "runs",
    )
    return registry, worker


def _wait_for_status(
    registry: JobRegistry,
    job_id: str,
    statuses: set[JobStatus],
    *,
    timeout_s: float = 5.0,
) -> JobRecord:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = registry.get_job(job_id)
        if record.status in statuses:
            return record
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for {statuses}; current={registry.get_job(job_id)}")


def test_normal_completion_preserves_artifact_and_full_audit(tmp_path: Path) -> None:
    registry, worker = _setup(tmp_path, "normal", duration_s=0.05)

    result = worker.run_once("worker-normal")

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    artifact_path = tmp_path / "runs" / result.job_id / "artifacts" / "temperature-summary.json"
    assert artifact_path.is_file()
    artifacts = registry.list_artifacts(result.job_id)
    artifact = next(item for item in artifacts if item.path.endswith("temperature-summary.json"))
    assert artifact.sha256 == hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert [event.to_status for event in registry.list_events(result.job_id)] == [
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


def test_crash_before_launch_becomes_failed_launch_without_killing_caller(tmp_path: Path) -> None:
    registry = JobRegistry(tmp_path / "jobs.sqlite")
    registry.create_job("job-before-launch", kind="fault", request={})
    worker = SubprocessJobWorker(
        registry,
        MissingExecutableFactory(tmp_path / "does-not-exist.exe"),
        runs_root=tmp_path / "runs",
    )

    result = worker.run_once("worker-launch")

    assert result is not None
    assert result.status is JobStatus.FAILED_LAUNCH
    assert result.error_code == "WORKER_LAUNCH_FAILED"
    registry.create_job("caller-still-alive", kind="proof", request={})


def test_crash_during_solve_becomes_failed_solver(tmp_path: Path) -> None:
    registry, worker = _setup(tmp_path, "crash_during_solve")

    result = worker.run_once("worker-crash")

    assert result is not None
    assert result.status is JobStatus.FAILED_SOLVER
    assert result.error_code == "WORKER_EXIT"
    assert JobStatus.SOLVING in [event.to_status for event in registry.list_events(result.job_id)]


def test_stopped_heartbeat_terminates_only_owned_worker(tmp_path: Path) -> None:
    registry, worker = _setup(
        tmp_path,
        "heartbeat_stop",
        heartbeat_timeout_s=0.08,
        duration_s=0.5,
    )

    result = worker.run_once("worker-heartbeat")

    assert result is not None
    assert result.status is JobStatus.FAILED_RESOURCE
    assert result.error_code == "HEARTBEAT_TIMEOUT"
    assert all(
        record.ended_at is not None for record in registry.list_owned_processes(result.job_id)
    )


def test_wall_clock_timeout_is_distinct_from_heartbeat_timeout(tmp_path: Path) -> None:
    _, worker = _setup(
        tmp_path,
        "wall_timeout",
        heartbeat_timeout_s=1.0,
        wall_clock_timeout_s=0.1,
        duration_s=0.5,
    )

    result = worker.run_once("worker-wall")

    assert result is not None
    assert result.status is JobStatus.FAILED_RESOURCE
    assert result.error_code == "WALL_CLOCK_TIMEOUT"


def test_active_cancel_reaches_cancelled_and_cleans_owned_worker(tmp_path: Path) -> None:
    registry, worker = _setup(tmp_path, "wait_for_cancel", duration_s=3.0)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker.run_once, "worker-cancel")
        _wait_for_status(registry, "job-wait_for_cancel", {JobStatus.SOLVING})
        registry.request_cancel("job-wait_for_cancel", message="fault injection")
        result = future.result(timeout=4)

    assert result is not None
    assert result.status is JobStatus.CANCELLED
    assert all(
        record.ended_at is not None for record in registry.list_owned_processes(result.job_id)
    )


def test_external_worker_kill_is_contained_as_failed_solver(tmp_path: Path) -> None:
    registry, worker = _setup(tmp_path, "wait_for_cancel", duration_s=3.0)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker.run_once, "worker-kill")
        _wait_for_status(registry, "job-wait_for_cancel", {JobStatus.SOLVING})
        owned = registry.list_owned_processes("job-wait_for_cancel", include_ended=False)
        assert len(owned) == 1
        psutil.Process(owned[0].pid).kill()
        result = future.result(timeout=4)

    assert result is not None
    assert result.status is JobStatus.FAILED_SOLVER
    assert result.error_code == "WORKER_EXIT"


def test_partial_artifact_is_registered_after_worker_failure(tmp_path: Path) -> None:
    registry, worker = _setup(tmp_path, "partial_artifact_failure")

    result = worker.run_once("worker-partial")

    assert result is not None
    assert result.status is JobStatus.FAILED_SOLVER
    partial = next(
        item
        for item in registry.list_artifacts(result.job_id)
        if item.path == "artifacts/partial.txt"
    )
    assert partial.size_bytes > 0
    assert (tmp_path / "runs" / result.job_id / partial.path).read_text(encoding="utf-8") == (
        "preserved partial evidence\n"
    )


def test_preexisting_or_pid_reused_process_is_never_terminated(tmp_path: Path) -> None:
    registry = JobRegistry(tmp_path / "jobs.sqlite")
    registry.create_job("ownership", kind="fault", request={})
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(10)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        actual = psutil.Process(process.pid)
        registry.register_process(
            OwnedProcessRecord(
                job_id="ownership",
                pid=process.pid,
                parent_pid=actual.ppid(),
                create_time=actual.create_time() + 100.0,
                executable_path=actual.exe(),
                command_line_fingerprint="0" * 64,
                launcher_pid=process.pid,
                discovery_method="pid_reuse_contract_fixture",
                registered_at="2026-08-23T08:00:00Z",
            )
        )

        report = ProcessSupervisor(registry).terminate_owned("ownership")

        assert report.identity_mismatches == (process.pid,)
        assert process.poll() is None
    finally:
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
