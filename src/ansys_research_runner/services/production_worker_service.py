"""Reviewed production Job Registry command and worker-dispatch services."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.jobs import JobRecord
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.job_registry import JobRegistry
from ansys_research_runner.services.job_worker_service import SubprocessJobWorker


@dataclass(frozen=True, slots=True)
class ProductionThermalCommandFactory:
    """Build fixed thermal worker argument vectors."""

    python_executable: str = sys.executable

    def build(self, job: JobRecord, job_dir: Path) -> list[str]:
        raw = job.request.get("cae_ir")
        if not isinstance(raw, dict):
            raise ValueError("Production jobs require a cae_ir object in their request.")
        cae_ir = ResolvedCAEIR.model_validate(raw)
        if cae_ir.run_id != job.job_id:
            raise ValueError("Production job ID differs from its CAE-IR run ID.")
        request_path = job_dir.resolve() / "control" / "cae_ir.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(request_path, cae_ir.model_dump(mode="json", by_alias=True))
        return [
            self.python_executable,
            "-m",
            "ansys_research_runner.adapters.worker.thermal_worker",
            "--job-dir",
            str(job_dir.resolve()),
            "--request",
            str(request_path),
        ]


def run_production_worker_once(
    *,
    paths: RunnerPaths | None = None,
    worker_id: str | None = None,
) -> JobRecord | None:
    """Claim and execute at most one queued production job."""

    active_paths = paths or RunnerPaths.from_environment()
    active_paths.ensure_runtime()
    worker = SubprocessJobWorker(
        JobRegistry(active_paths.database),
        ProductionThermalCommandFactory(),
        runs_root=active_paths.runs,
        poll_interval_s=0.1,
        lease_seconds=60.0,
    )
    identity = worker_id or f"production-{os.getpid()}"
    return worker.run_once(identity)


class BackgroundWorkerDispatcher:
    """Start a detached queue drainer without holding any solver session."""

    def __init__(self, paths: RunnerPaths | None = None) -> None:
        self._paths = paths or RunnerPaths.from_environment()

    def dispatch(self) -> None:
        """Start one best-effort drainer; the drainer lock enforces concurrency one."""

        environment: dict[str, Any] = os.environ.copy()
        environment["ANSYS_RESEARCH_ROOT"] = str(self._paths.root)
        environment["ANSYS_RESEARCH_RUNTIME"] = str(self._paths.runtime)
        creation_flags = 0
        if os.name == "nt":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(  # noqa: S603 - fixed internal module command
            [
                sys.executable,
                "-m",
                "ansys_research_runner.adapters.worker.queue_dispatcher",
            ],
            cwd=self._paths.root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creation_flags,
            close_fds=True,
        )
