"""G5 official Prime/MAPDL/DPF transient-thermal capability assessment."""

from __future__ import annotations

from ansys_research_runner.adapters.solver.mapdl import MapdlSolverAdapter
from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.capabilities import (
    CapabilityStatus,
    SolverLaunchCapability,
    TransientSolverGateCapabilityReport,
)
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.capability_service import utc_now
from ansys_research_runner.services.live_thermal_gate_service import (
    run_actual_transient_gate_cases,
    successful_case_names,
)


def _launch_capability(adapter: MapdlSolverAdapter) -> SolverLaunchCapability:
    capability = adapter.probe_capabilities()
    return SolverLaunchCapability(
        mode="prime_mapdl_batch",
        status=(CapabilityStatus.AVAILABLE if capability.available else CapabilityStatus.BLOCKED),
        package_version=capability.package_version,
        product_version=capability.product_version,
        reason=capability.reason,
        evidence={
            **capability.evidence,
            "backend": capability.backend,
            "launch_mode": capability.launch_mode,
            "capabilities": list(capability.capabilities),
            "owned_process_cleanup": {"remaining": []},
        },
    )


def collect_transient_solver_capabilities(
    *,
    probe_timeout_seconds: float = 180,
) -> TransientSolverGateCapabilityReport:
    """Run both required transient cases through the production solver path."""

    adapter = MapdlSolverAdapter(
        prime_timeout_s=probe_timeout_seconds,
        solve_timeout_s=probe_timeout_seconds,
    )
    try:
        launch = _launch_capability(adapter)
    finally:
        adapter.close()

    cases: dict[str, dict[str, object]] = {}
    failure: str | None = None
    if launch.status is CapabilityStatus.AVAILABLE:
        try:
            cases = run_actual_transient_gate_cases(probe_timeout_seconds=probe_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - capability report must preserve live failure
            failure = f"{type(exc).__name__}: {exc}"

    actual_cases = successful_case_names(cases)
    worker_qualified = len(actual_cases) == 2
    ready = launch.status is CapabilityStatus.AVAILABLE and worker_qualified
    reasons: list[str] = []
    if launch.status is not CapabilityStatus.AVAILABLE:
        reasons.append(launch.reason or "Prime/MAPDL/DPF backend is unavailable.")
    if failure is not None:
        reasons.append(f"Actual transient solve failed: {failure}")
    if not worker_qualified:
        reasons.append(f"Required actual transient cases passed: {len(actual_cases)}/2.")
    launch_with_cases = launch.model_copy(
        update={"evidence": {**launch.evidence, "actual_cases": cases}}
    )
    return TransientSolverGateCapabilityReport(
        generated_at=utc_now(),
        status="PASSED" if ready else "BLOCKED_ENVIRONMENT",
        launch_modes=(launch_with_cases,),
        thermal_worker_live_qualified=worker_qualified,
        actual_cases_succeeded=actual_cases,
        blocker_reason=" ".join(reasons) or None,
    )


def persist_transient_solver_capabilities(report: TransientSolverGateCapabilityReport) -> None:
    """Persist machine-local transient-solver evidence under the ignored runtime root."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    atomic_write_json(
        paths.runtime / "transient_solver_capability_report.json",
        report.model_dump(mode="json"),
    )
    atomic_write_json(
        paths.blockers / "G5.json",
        {
            "gate": "G5",
            "status": report.status,
            "launch_modes": [item.model_dump(mode="json") for item in report.launch_modes],
            "thermal_worker_live_qualified": report.thermal_worker_live_qualified,
            "actual_cases_succeeded": list(report.actual_cases_succeeded),
            "required_cases": list(report.required_cases),
            "reason": report.blocker_reason,
            "reproduction_command": "ansys-research solver-doctor --live --json",
        },
    )
