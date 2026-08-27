"""Isolated production thermal worker consuming only reviewed CAE-IR."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from ansys_research_runner.adapters.solver.base import RunCallbacks, SolverAdapter
from ansys_research_runner.adapters.solver.mapdl import MapdlSolverAdapter
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.results import ExecutionStatus
from ansys_research_runner.io import atomic_write_json, atomic_write_text


def _emit(control: Path, status: str, detail: str) -> None:
    control.mkdir(parents=True, exist_ok=True)
    with (control / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                {"type": "phase", "status": status, "detail": detail},
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        stream.flush()


def _heartbeat(control: Path) -> None:
    atomic_write_text(control / "heartbeat.txt", f"{time.time():.9f}\n")


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def _write_failure(artifacts: Path, phase: str, message: str, **details: object) -> None:
    atomic_write_json(
        artifacts / "worker-error.json",
        {"schema_version": 1, "phase": phase, "message": message, "details": details},
    )


def run(
    job_dir: Path,
    request_path: Path,
    *,
    adapter_factory: Callable[[], SolverAdapter] = MapdlSolverAdapter,
) -> int:
    """Execute one staged CAE-IR and export only confined job artifacts."""

    resolved_job_dir = job_dir.resolve()
    control = resolved_job_dir / "control"
    artifacts = resolved_job_dir / "artifacts"
    results = resolved_job_dir / "results"
    for directory in (control, artifacts, results):
        directory.mkdir(parents=True, exist_ok=True)
    _heartbeat(control)
    try:
        cae_ir = ResolvedCAEIR.model_validate_json(
            request_path.resolve().read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        _write_failure(artifacts, "PRECHECKING", "Staged CAE-IR is invalid.", error=str(exc))
        return 11
    if cae_ir.run_id != resolved_job_dir.name:
        _write_failure(
            artifacts,
            "PRECHECKING",
            "CAE-IR run ID does not match its job directory.",
            cae_ir_run_id=cae_ir.run_id,
            job_directory=resolved_job_dir.name,
        )
        return 11

    adapter = adapter_factory()
    prepared = adapter.prepare(cae_ir, resolved_job_dir / "work")
    try:
        precheck = adapter.precheck(prepared)
        if not precheck.valid:
            _write_failure(
                artifacts,
                "PRECHECKING",
                "Solver precheck rejected the CAE-IR.",
                issues=[item.model_dump(mode="json") for item in precheck.issues],
            )
            return 12

        _emit(control, "SOLVING", "Prime meshing and MAPDL solve entered")
        _heartbeat(control)
        callbacks = RunCallbacks(
            heartbeat=lambda: _heartbeat(control),
            log=lambda message: print(message, flush=True),
        )
        solve_result = adapter.solve(prepared, callbacks)
        atomic_write_json(artifacts / "solve-result.json", solve_result.model_dump(mode="json"))
        for name, path in solve_result.result_files.items():
            _copy_file(path, artifacts / f"solver-{name}{path.suffix.lower()}")
        if solve_result.status is not ExecutionStatus.SUCCEEDED:
            _write_failure(
                artifacts,
                "SOLVING",
                solve_result.message or "The solver did not complete successfully.",
                execution_status=solve_result.status.value,
                evidence=solve_result.evidence,
            )
            return 20

        _emit(control, "POSTPROCESSING", "DPF result extraction entered")
        _heartbeat(control)
        try:
            postprocess = adapter.postprocess(prepared, solve_result)
        except Exception as exc:  # noqa: BLE001 - converted at isolated worker boundary
            _write_failure(
                artifacts,
                "POSTPROCESSING",
                "DPF result extraction failed.",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return 30

        _emit(control, "EXPORTING", "Confined result artifact export entered")
        _heartbeat(control)
        summary = postprocess.observation.summary.model_dump(mode="json")
        atomic_write_json(results / "summary.json", summary)
        atomic_write_json(artifacts / "summary.json", summary)
        atomic_write_json(
            artifacts / "observation.json",
            postprocess.observation.model_dump(mode="json"),
        )
        if postprocess.field_path is not None:
            _copy_file(postprocess.field_path, artifacts / "temperature-field.h5")
        atomic_write_json(
            artifacts / "worker-result.json",
            {
                "schema_version": 1,
                "run_id": cae_ir.run_id,
                "execution_status": solve_result.status.value,
                "solver_converged": solve_result.converged,
                "mesh_sha256": postprocess.mesh_sha256,
                "summary_path": "results/summary.json",
                "field_path": (
                    "artifacts/temperature-field.h5" if postprocess.field_path is not None else None
                ),
            },
        )
        _heartbeat(control)
        return 0
    finally:
        adapter.close()


def main(argv: list[str] | None = None) -> int:
    """Run one production thermal job from the reviewed command factory."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.job_dir, args.request)


if __name__ == "__main__":
    raise SystemExit(main())
