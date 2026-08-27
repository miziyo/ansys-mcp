"""Fail-safe official PyMechanical solver adapter boundary."""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ansys_research_runner.adapters.solver.base import (
    PostprocessResult,
    PreparedRun,
    RunCallbacks,
    SolverCapabilityReport,
    SolveResult,
)
from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.cae_ir import BackendTarget, ResolvedCAEIR
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.results import ExecutionStatus
from ansys_research_runner.domain.validation import (
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from ansys_research_runner.io import atomic_write_text
from ansys_research_runner.services.contract_service import deterministic_json


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PyMechanicalSolverAdapter:
    """PyMechanical boundary that refuses execution unless a live probe proved availability."""

    def __init__(self, capability_report_path: Path | None = None) -> None:
        paths = RunnerPaths.from_environment()
        self._capability_report_path = capability_report_path or (
            paths.runtime / "capability_report.json"
        )
        self._closed = False

    def probe_capabilities(self) -> SolverCapabilityReport:
        """Read the persisted isolated live-probe result for direct Mechanical."""

        try:
            package_version = version("ansys-mechanical-core")
        except PackageNotFoundError:
            package_version = None
        if not self._capability_report_path.is_file():
            return SolverCapabilityReport(
                backend="pymechanical",
                available=False,
                package_version=package_version,
                launch_mode="remote_grpc",
                reason="LIVE_CAPABILITY_REPORT_MISSING",
            )
        payload = json.loads(self._capability_report_path.read_text(encoding="utf-8"))
        mechanical: dict[str, object] = {}
        for item in payload.get("products", []):
            if isinstance(item, dict) and item.get("product") == "mechanical":
                mechanical = item
                break
        live_status = str(payload.get("required_mechanical_live", "not_probed"))
        available = live_status == "available" and not self._closed
        return SolverCapabilityReport(
            backend="pymechanical",
            available=available,
            package_version=package_version,
            product_version="26.1",
            launch_mode="remote_grpc",
            capabilities=(
                "prepare",
                "precheck",
                "steady_thermal",
                "transient_thermal",
                "postprocess",
            )
            if available
            else ("prepare", "precheck"),
            reason=(
                "ADAPTER_CLOSED"
                if self._closed
                else None
                if available
                else str(mechanical.get("reason") or f"MECHANICAL_{live_status.upper()}")
            ),
            evidence={
                "live_status": live_status,
                "details": mechanical.get("details", {}),
            },
        )

    def prepare(self, cae_ir: ResolvedCAEIR, workdir: Path) -> PreparedRun:
        """Write the only solver input contract beneath a run-owned work directory."""

        if self._closed:
            raise RuntimeError("PyMechanical solver adapter is closed.")
        resolved_workdir = workdir.resolve()
        resolved_workdir.mkdir(parents=True, exist_ok=True)
        solver_output = resolved_workdir / "solver-output"
        solver_output.mkdir(exist_ok=True)
        cae_ir_path = resolved_workdir / "cae_ir.json"
        atomic_write_text(cae_ir_path, deterministic_json(cae_ir) + "\n")
        return PreparedRun(
            run_id=cae_ir.run_id,
            workdir=resolved_workdir,
            cae_ir_path=cae_ir_path,
            source_model_path=Path(cae_ir.geometry.file).expanduser().resolve(),
            solver_output_dir=solver_output,
        )

    def precheck(self, prepared: PreparedRun) -> ValidationReport:
        """Validate source identity, target backend, and staged CAE-IR integrity."""

        issues: list[ValidationIssue] = []
        try:
            cae_ir = ResolvedCAEIR.model_validate_json(
                prepared.cae_ir_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            issues.append(
                ValidationIssue(
                    code="CAE_IR_INVALID",
                    path="resolved/cae_ir.json",
                    message=str(exc),
                )
            )
            return ValidationReport(valid=False, issues=tuple(issues))
        if cae_ir.backend_target is not BackendTarget.PYMECHANICAL:
            issues.append(
                ValidationIssue(
                    code="BACKEND_TARGET_MISMATCH",
                    path="backend_target",
                    message="PyMechanical adapter requires backend_target=pymechanical.",
                )
            )
        if not prepared.source_model_path.is_file():
            issues.append(
                ValidationIssue(
                    code=ErrorCode.SOURCE_MODEL_MISMATCH.value,
                    path="geometry.file",
                    message="Source geometry file does not exist.",
                    details={"path": str(prepared.source_model_path)},
                )
            )
        elif _sha256_file(prepared.source_model_path) != cae_ir.geometry.sha256:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.SOURCE_MODEL_MISMATCH.value,
                    path="geometry.sha256",
                    message="Source geometry hash differs from the compiled CAE-IR.",
                )
            )
        capability = self.probe_capabilities()
        if not capability.available:
            issues.append(
                ValidationIssue(
                    code=ErrorCode.SOLVER_CAPABILITY_MISSING.value,
                    path="backend.pymechanical",
                    message=capability.reason or "PyMechanical live capability is unavailable.",
                    severity=ValidationSeverity.ERROR,
                    details=capability.evidence,
                )
            )
        return ValidationReport(valid=not issues, issues=tuple(issues))

    def solve(self, prepared: PreparedRun, callbacks: RunCallbacks) -> SolveResult:
        """Fail before launch when the required live capability is not proven."""

        del callbacks
        precheck = self.precheck(prepared)
        if not precheck.valid:
            capability_missing = any(
                issue.code == ErrorCode.SOLVER_CAPABILITY_MISSING.value for issue in precheck.issues
            )
            return SolveResult(
                run_id=prepared.run_id,
                status=(
                    ExecutionStatus.BLOCKED_ENVIRONMENT
                    if capability_missing
                    else ExecutionStatus.FAILED_PRECHECK
                ),
                converged=None,
                message="; ".join(issue.message for issue in precheck.issues),
                evidence={"issues": [issue.model_dump(mode="json") for issue in precheck.issues]},
            )
        raise DomainError(
            ErrorCode.SOLVER_CAPABILITY_MISSING,
            "backend.pymechanical.worker",
            "The reviewed Mechanical thermal worker has not yet been live-qualified.",
        )

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        """Reject postprocessing when no successful solver result exists."""

        del prepared
        raise DomainError(
            ErrorCode.SOLVER_CAPABILITY_MISSING,
            "postprocess",
            f"No postprocessable Mechanical result exists ({solve_result.status.value}).",
        )

    def request_cancel(self, prepared: PreparedRun) -> None:
        """Persist a bounded cancellation marker for the future worker supervisor."""

        marker = prepared.workdir / "cancel.requested"
        atomic_write_text(marker, prepared.run_id + "\n")

    def close(self) -> None:
        """Mark the adapter closed; it does not own a long-lived solver session."""

        self._closed = True
