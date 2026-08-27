"""Production command-factory and isolated thermal-worker tests."""

from __future__ import annotations

import json
from pathlib import Path

from ansys_research_runner.adapters.solver.base import (
    PostprocessResult,
    PreparedRun,
    RunCallbacks,
    SolverCapabilityReport,
    SolveResult,
)
from ansys_research_runner.adapters.worker.thermal_worker import run
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    ScalarResultSummary,
    TemperatureSummary,
    ThermalObservation,
)
from ansys_research_runner.domain.validation import ValidationReport
from ansys_research_runner.io import atomic_write_text
from ansys_research_runner.services.production_worker_service import (
    ProductionThermalCommandFactory,
)
from tests.g8_fixtures import build_g8_workspace


class _FakeSolverAdapter:
    def probe_capabilities(self) -> SolverCapabilityReport:
        return SolverCapabilityReport(backend="fake", available=True)

    def prepare(self, cae_ir: ResolvedCAEIR, workdir: Path) -> PreparedRun:
        workdir.mkdir(parents=True, exist_ok=True)
        output = workdir / "solver-output"
        output.mkdir()
        cae_ir_path = workdir / "cae_ir.json"
        atomic_write_text(cae_ir_path, cae_ir.model_dump_json())
        return PreparedRun(
            run_id=cae_ir.run_id,
            workdir=workdir,
            cae_ir_path=cae_ir_path,
            source_model_path=Path(cae_ir.geometry.file),
            solver_output_dir=output,
        )

    def precheck(self, prepared: PreparedRun) -> ValidationReport:
        del prepared
        return ValidationReport(valid=True)

    def solve(self, prepared: PreparedRun, callbacks: RunCallbacks) -> SolveResult:
        callbacks.heartbeat()
        result = prepared.solver_output_dir / "thermal.rst"
        result.write_bytes(b"fake-result")
        return SolveResult(
            run_id=prepared.run_id,
            status=ExecutionStatus.SUCCEEDED,
            converged=True,
            exit_code=0,
            result_files={"result": result},
        )

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        del solve_result
        field = prepared.solver_output_dir / "temperature.h5"
        field.write_bytes(b"fake-field")
        return PostprocessResult(
            observation=ThermalObservation(
                summary=ScalarResultSummary(
                    temperature=TemperatureSummary(
                        minimum_K=293.15,
                        maximum_K=333.15,
                        volume_average_K=313.15,
                    )
                )
            ),
            field_path=field,
            mesh_sha256="0" * 64,
        )

    def request_cancel(self, prepared: PreparedRun) -> None:
        del prepared

    def close(self) -> None:
        return None


def test_production_command_factory_stages_only_validated_cae_ir(tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    submitted = service.run(recipe_path, run_id="production-command")
    job_dir = service.paths.runs / submitted.job.job_id

    command = ProductionThermalCommandFactory().build(submitted.job, job_dir)

    assert command[1:3] == ["-m", "ansys_research_runner.adapters.worker.thermal_worker"]
    request_path = job_dir / "control" / "cae_ir.json"
    staged = ResolvedCAEIR.model_validate_json(request_path.read_text(encoding="utf-8"))
    assert staged.run_id == submitted.job.job_id
    assert "recipe_path" not in json.loads(request_path.read_text(encoding="utf-8"))


def test_isolated_worker_exports_bounded_summary_and_field_artifact(tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    plan = service.plan(recipe_path, run_id="production-worker")
    job_dir = service.paths.runs / plan.run_id
    request = job_dir / "control" / "cae_ir.json"
    request.parent.mkdir(parents=True)
    atomic_write_text(request, plan.cae_ir.model_dump_json(by_alias=True))

    return_code = run(job_dir, request, adapter_factory=_FakeSolverAdapter)

    assert return_code == 0
    assert (job_dir / "results" / "summary.json").is_file()
    assert (job_dir / "artifacts" / "temperature-field.h5").read_bytes() == b"fake-field"
    worker_result = json.loads(
        (job_dir / "artifacts" / "worker-result.json").read_text(encoding="utf-8")
    )
    assert worker_result["execution_status"] == "SUCCEEDED"
    phases = [
        json.loads(line)["status"]
        for line in (job_dir / "control" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert phases == ["SOLVING", "POSTPROCESSING", "EXPORTING"]
