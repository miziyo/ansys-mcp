"""Actual G7 PyWorkbench lifecycle and Mechanical handoff capability test."""

from __future__ import annotations

import pytest

from ansys_research_runner.services.workbench_capability_service import (
    collect_workbench_coupling_capability,
    persist_workbench_coupling_capability,
)


@pytest.mark.ansys_live
def test_actual_workbench_coupling_capability_is_explicit_and_clean() -> None:
    report = collect_workbench_coupling_capability(probe_timeout_seconds=360)
    persist_workbench_coupling_capability(report)

    assert report.status in {"PASSED", "BLOCKED_ENVIRONMENT"}
    assert report.owned_cleanup_verified
    if report.status == "PASSED":
        assert report.lifecycle_complete
        assert report.reviewed_script_execution
        assert report.project_created_saved_reopened
        assert report.project is not None and report.project.sha256
        assert report.archive is not None and report.archive.sha256
        if report.workbench_coupling:
            assert report.mechanical_server_started
            assert report.pymechanical_handoff
            assert report.coupling_probe_completed
        else:
            assert report.blocker_stage in {"pymechanical_handoff", "coupling_shutdown"}
            assert report.blocker_reason
    else:
        assert not report.workbench_coupling
        assert report.blocker_reason
