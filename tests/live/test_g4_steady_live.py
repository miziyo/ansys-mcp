"""Actual G4 Prime/MAPDL/DPF evidence; skips never count as Gate success."""

from __future__ import annotations

import pytest

from ansys_research_runner.domain.capabilities import CapabilityStatus
from ansys_research_runner.services.solver_capability_service import (
    collect_steady_solver_capabilities,
    persist_steady_solver_capabilities,
)


@pytest.mark.ansys_live
def test_actual_mechanical_steady_capability_is_explicit() -> None:
    report = collect_steady_solver_capabilities(probe_timeout_seconds=180)
    persist_steady_solver_capabilities(report)
    assert report.status in {"PASSED", "BLOCKED_ENVIRONMENT"}
    assert {item.mode for item in report.launch_modes} == {"prime_mapdl_batch"}
    for mode in report.launch_modes:
        cleanup = mode.evidence.get("owned_process_cleanup")
        if isinstance(cleanup, dict):
            assert cleanup.get("remaining") == []
    if report.status == "PASSED":
        assert report.steady_solver_ready
        assert set(report.required_cases).issubset(report.actual_cases_succeeded)
        assert report.blocker_reason is None
    else:
        assert not report.steady_solver_ready
        assert report.blocker_reason
        assert (
            any(mode.status is not CapabilityStatus.AVAILABLE for mode in report.launch_modes)
            or not report.thermal_worker_live_qualified
        )
