"""G6 optional production thermal lifecycle smoke and capability persistence."""

from __future__ import annotations

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.capabilities import (
    CapabilityStatus,
    JobInfrastructureLiveCapabilityReport,
)
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.capability_service import utc_now
from ansys_research_runner.services.solver_capability_service import (
    collect_steady_solver_capabilities,
)


def collect_job_infrastructure_live_capability(
    *, probe_timeout_seconds: float = 180
) -> JobInfrastructureLiveCapabilityReport:
    """Run the production thermal path without making it a G6 core prerequisite."""

    launch_report = collect_steady_solver_capabilities(probe_timeout_seconds=probe_timeout_seconds)
    lifecycle_completed = any(
        mode.status is CapabilityStatus.AVAILABLE for mode in launch_report.launch_modes
    )
    cleanup_verified = True
    for mode in launch_report.launch_modes:
        cleanup = mode.evidence.get("owned_process_cleanup")
        if not isinstance(cleanup, dict) or cleanup.get("remaining") != []:
            cleanup_verified = False
    reason = None
    if not lifecycle_completed:
        reason = (
            "The production thermal lifecycle smoke is unavailable; the solver-neutral G6 "
            "fault matrix remains independent."
        )
    return JobInfrastructureLiveCapabilityReport(
        generated_at=utc_now(),
        status="AVAILABLE" if lifecycle_completed else "BLOCKED_ENVIRONMENT",
        launch_modes=launch_report.launch_modes,
        mechanical_lifecycle_completed=lifecycle_completed,
        owned_cleanup_verified=cleanup_verified,
        reason=reason,
    )


def persist_job_infrastructure_live_capability(
    report: JobInfrastructureLiveCapabilityReport,
) -> None:
    """Persist the G6 production-worker live-smoke result."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    atomic_write_json(
        paths.runtime / "g6_mechanical_smoke_report.json",
        report.model_dump(mode="json"),
    )
