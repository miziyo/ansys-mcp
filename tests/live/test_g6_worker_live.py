"""Actual G6 production thermal lifecycle smoke; no skip is accepted by the Gate."""

from __future__ import annotations

import pytest

from ansys_research_runner.services.job_capability_service import (
    collect_job_infrastructure_live_capability,
    persist_job_infrastructure_live_capability,
)


@pytest.mark.ansys_live
def test_actual_production_lifecycle_smoke_is_explicit_and_clean() -> None:
    report = collect_job_infrastructure_live_capability(probe_timeout_seconds=180)
    persist_job_infrastructure_live_capability(report)

    assert report.status in {"AVAILABLE", "BLOCKED_ENVIRONMENT"}
    assert report.owned_cleanup_verified
    assert {mode.mode for mode in report.launch_modes} == {"prime_mapdl_batch"}
    if report.status == "AVAILABLE":
        assert report.mechanical_lifecycle_completed
    else:
        assert not report.mechanical_lifecycle_completed
        assert report.reason
