"""G10 mesh-study aggregation and partial-failure contracts."""

from __future__ import annotations

from pathlib import Path

from ansys_research_runner.domain.mesh_study import MeshStudyChild, MeshStudyExecution
from ansys_research_runner.domain.recipe import MeshIntent, MeshVerificationRequest
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.services.mesh_study_service import (
    aggregate_mesh_study,
    aggregate_persisted_mesh_study,
    persist_mesh_study_inputs,
)


def _child(
    tmp_path: Path,
    profile: MeshIntent,
    *,
    value: float | None,
    elements: int | None,
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
) -> MeshStudyChild:
    workdir = tmp_path / profile.value
    workdir.mkdir()
    return MeshStudyChild(
        profile=profile,
        run_id=f"study-{profile.value}",
        workdir=workdir,
        execution_status=status,
        physical_verification_status=(
            VerificationStatus.PASSED
            if status is ExecutionStatus.SUCCEEDED
            else VerificationStatus.NOT_RUN
        ),
        element_count=elements,
        runtime_s=1.0,
        target_metric_value=value,
    )


def test_three_independent_profiles_pass_practical_tolerance(tmp_path: Path) -> None:
    criterion = MeshVerificationRequest(relative_tolerance=0.005)
    children = (
        _child(tmp_path, MeshIntent.COARSE, value=320.0, elements=100),
        _child(tmp_path, MeshIntent.BALANCED, value=321.0, elements=500),
        _child(tmp_path, MeshIntent.FINE, value=321.5, elements=2000),
    )

    result = aggregate_mesh_study("study", children, criterion)

    assert result.execution is MeshStudyExecution.SUCCEEDED
    assert result.mesh_verification is VerificationStatus.PASSED
    assert result.reference_profile is MeshIntent.FINE
    assert [item.element_count for item in result.children] == [100, 500, 2000]
    assert result.children[1].relative_difference == (321.5 - 321.0) / 321.5
    assert len({item.workdir for item in result.children}) == 3


def test_one_child_failure_is_partial_and_inconclusive(tmp_path: Path) -> None:
    criterion = MeshVerificationRequest(relative_tolerance=0.005)
    children = (
        _child(tmp_path, MeshIntent.COARSE, value=320.0, elements=100),
        _child(tmp_path, MeshIntent.BALANCED, value=321.0, elements=500),
        _child(
            tmp_path,
            MeshIntent.FINE,
            value=None,
            elements=None,
            status=ExecutionStatus.FAILED_SOLVER,
        ),
    )

    result = aggregate_mesh_study("partial", children, criterion)

    assert result.execution is MeshStudyExecution.PARTIAL
    assert result.mesh_verification is VerificationStatus.INCONCLUSIVE
    assert result.children[0].execution_status is ExecutionStatus.SUCCEEDED


def test_aggregation_can_be_restarted_without_child_execution(tmp_path: Path) -> None:
    criterion = MeshVerificationRequest(absolute_tolerance_K=1.0)
    children = (
        _child(tmp_path, MeshIntent.COARSE, value=320.0, elements=100),
        _child(tmp_path, MeshIntent.BALANCED, value=321.0, elements=500),
        _child(tmp_path, MeshIntent.FINE, value=321.4, elements=2000),
    )
    study_root = tmp_path / "persisted"
    persist_mesh_study_inputs(
        study_root,
        study_id="restartable",
        criterion=criterion,
        children=children,
    )

    first = aggregate_persisted_mesh_study(study_root)
    second = aggregate_persisted_mesh_study(study_root)

    assert first == second
    assert (study_root / "study-result.json").is_file()
