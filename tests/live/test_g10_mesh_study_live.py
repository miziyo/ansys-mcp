"""Actual three-level MAPDL mesh-refinement study evidence."""

from __future__ import annotations

import pytest

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.mesh_study import MeshStudyExecution
from ansys_research_runner.domain.recipe import MeshIntent
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.mesh_study_service import (
    aggregate_persisted_mesh_study,
    run_actual_mesh_verification_study,
)


@pytest.mark.ansys_live
def test_actual_three_profile_mesh_study_passes_and_is_restartable() -> None:
    result = run_actual_mesh_verification_study(probe_timeout_seconds=300.0)

    assert result.execution is MeshStudyExecution.SUCCEEDED
    assert result.mesh_verification is VerificationStatus.PASSED
    assert {child.profile for child in result.children} == set(MeshIntent)
    assert all(child.execution_status is ExecutionStatus.SUCCEEDED for child in result.children)
    assert all(
        child.physical_verification_status is VerificationStatus.PASSED for child in result.children
    )
    counts = [child.element_count for child in result.children]
    assert counts[0] is not None and counts[1] is not None and counts[2] is not None
    assert counts[0] < counts[1] < counts[2]
    study_root = result.children[0].workdir.parent.parent
    assert aggregate_persisted_mesh_study(study_root) == result
    atomic_write_json(
        RunnerPaths.from_environment().runtime / "g10_mesh_study_report.json",
        result.model_dump(mode="json"),
    )
