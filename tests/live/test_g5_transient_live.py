"""Actual G5 Prime/MAPDL/DPF evidence; live skips are forbidden by the Gate."""

from __future__ import annotations

import pytest

from ansys_research_runner.services.transient_capability_service import (
    collect_transient_solver_capabilities,
    persist_transient_solver_capabilities,
)


@pytest.mark.ansys_live
def test_actual_mechanical_transient_capability_is_explicit() -> None:
    report = collect_transient_solver_capabilities(probe_timeout_seconds=180)
    persist_transient_solver_capabilities(report)
    assert report.status in {"PASSED", "BLOCKED_ENVIRONMENT"}
    assert {item.mode for item in report.launch_modes} == {"prime_mapdl_batch"}
    for mode in report.launch_modes:
        cleanup = mode.evidence.get("owned_process_cleanup")
        if isinstance(cleanup, dict):
            assert cleanup.get("remaining") == []
    if report.status == "PASSED":
        assert report.transient_solver_ready
        assert set(report.required_cases).issubset(report.actual_cases_succeeded)
        assert report.blocker_reason is None
    else:
        assert not report.transient_solver_ready
        assert report.blocker_reason
