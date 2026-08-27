"""Independent child execution and restartable mesh-study aggregation."""

from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path

import h5py  # type: ignore[import-untyped]

from ansys_research_runner.adapters.geometry.base import TestGeometryKind
from ansys_research_runner.adapters.solver.mapdl import MapdlSolverAdapter
from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.mesh_study import (
    MeshStudyChild,
    MeshStudyExecution,
    MeshStudyResult,
)
from ansys_research_runner.domain.recipe import MeshIntent, MeshVerificationRequest
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.live_thermal_gate_service import (
    _blueprint,
    _box_manifest,
    _mesh_policy,
    _source_graph,
    _steady_box_recipe,
)
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.steady_run_service import (
    SteadyReferenceKind,
    execute_steady_run,
)


def _within_tolerance(
    absolute: float,
    relative: float,
    criterion: MeshVerificationRequest,
) -> bool:
    checks: list[bool] = []
    if criterion.absolute_tolerance_K is not None:
        checks.append(absolute <= criterion.absolute_tolerance_K)
    if criterion.relative_tolerance is not None:
        checks.append(relative <= criterion.relative_tolerance)
    return any(checks)


def aggregate_mesh_study(
    study_id: str,
    children: tuple[MeshStudyChild, ...],
    criterion: MeshVerificationRequest,
) -> MeshStudyResult:
    """Aggregate persisted child observations without executing a solver."""

    by_profile = {child.profile: child for child in children}
    successful = {
        profile: child
        for profile, child in by_profile.items()
        if child.execution_status is ExecutionStatus.SUCCEEDED
        and child.target_metric_value is not None
    }
    if len(successful) == len(criterion.profiles):
        execution = MeshStudyExecution.SUCCEEDED
    elif successful:
        execution = MeshStudyExecution.PARTIAL
    else:
        execution = MeshStudyExecution.FAILED

    reference_profile = next(
        (profile for profile in reversed(criterion.profiles) if profile in successful),
        None,
    )
    reference_value = (
        None if reference_profile is None else successful[reference_profile].target_metric_value
    )
    updated: list[MeshStudyChild] = []
    for child in children:
        value = child.target_metric_value
        if value is None or reference_value is None:
            updated.append(child)
            continue
        absolute = abs(value - reference_value)
        denominator = max(abs(reference_value), 1.0e-300)
        updated.append(
            child.model_copy(
                update={
                    "absolute_difference": absolute,
                    "relative_difference": absolute / denominator,
                }
            )
        )

    balanced = next((item for item in updated if item.profile is MeshIntent.BALANCED), None)
    fine = next((item for item in updated if item.profile is MeshIntent.FINE), None)
    if execution is not MeshStudyExecution.SUCCEEDED or balanced is None or fine is None:
        verification = VerificationStatus.INCONCLUSIVE
    else:
        balanced_absolute = balanced.absolute_difference
        balanced_relative = balanced.relative_difference
        if (
            balanced_absolute is None
            or balanced_relative is None
            or not all(map(math.isfinite, (balanced_absolute, balanced_relative)))
        ):
            verification = VerificationStatus.INCONCLUSIVE
        else:
            verification = (
                VerificationStatus.PASSED
                if _within_tolerance(balanced_absolute, balanced_relative, criterion)
                else VerificationStatus.FAILED
            )
    return MeshStudyResult(
        study_id=study_id,
        execution=execution,
        mesh_verification=verification,
        target_metric=criterion.target_metric,
        criterion=criterion,
        reference_profile=reference_profile,
        children=tuple(updated),
    )


def persist_mesh_study_inputs(
    study_root: Path,
    *,
    study_id: str,
    criterion: MeshVerificationRequest,
    children: tuple[MeshStudyChild, ...],
) -> None:
    """Persist raw child observations separately from derived aggregation."""

    study_root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        study_root / "study-inputs.json",
        {
            "schema_version": 1,
            "study_id": study_id,
            "criterion": criterion.model_dump(mode="json"),
            "children": [child.model_dump(mode="json") for child in children],
        },
    )


def aggregate_persisted_mesh_study(study_root: Path) -> MeshStudyResult:
    """Recompute and persist aggregation using only saved child observations."""

    raw = json.loads((study_root / "study-inputs.json").read_text(encoding="utf-8"))
    criterion = MeshVerificationRequest.model_validate(raw["criterion"])
    children = tuple(MeshStudyChild.model_validate(item) for item in raw["children"])
    result = aggregate_mesh_study(str(raw["study_id"]), children, criterion)
    atomic_write_json(study_root / "study-result.json", result.model_dump(mode="json"))
    return result


def _target_metric(bundle: Path, name: str) -> float:
    summary = json.loads((bundle / "results" / "summary.json").read_text(encoding="utf-8"))
    temperature = summary["temperature"]
    key = {
        "minimum_temperature_K": "minimum_K",
        "maximum_temperature_K": "maximum_K",
        "volume_average_temperature_K": "volume_average_K",
    }[name]
    return float(temperature[key])


def _element_count(bundle: Path) -> int:
    with h5py.File(bundle / "results" / "temperature_field.h5", "r") as source:
        return int(source["/mesh/element_ids"].shape[0])


def run_actual_mesh_verification_study(
    *,
    probe_timeout_seconds: float = 240.0,
) -> MeshStudyResult:
    """Run coarse/balanced/fine as independent actual Ansys child bundles."""

    paths = RunnerPaths.from_environment()
    suffix = uuid.uuid4().hex[:12]
    study_id = f"g10-mesh-{suffix}"
    study_root = paths.runtime / "live-runs" / "G10" / study_id
    child_root = study_root / "children"
    source = resource_path("geometry", "g3_box.step")
    manifest = _box_manifest(source)
    graph = _source_graph(TestGeometryKind.BOX, source)
    criterion = MeshVerificationRequest(
        target_metric="volume_average_temperature_K",
        relative_tolerance=0.005,
    )
    base_recipe = _steady_box_recipe(study_root / "box.manifest.yaml").model_copy(
        update={"mesh_verification": criterion}
    )
    bundle_service = RunBundleService(child_root)
    adapter = MapdlSolverAdapter(
        prime_timeout_s=probe_timeout_seconds,
        solve_timeout_s=probe_timeout_seconds,
    )
    children: list[MeshStudyChild] = []
    try:
        for profile in criterion.profiles:
            run_id = f"{study_id}-{profile.value}"
            recipe = base_recipe.model_copy(
                update={"mesh": base_recipe.mesh.model_copy(update={"intent": profile})}
            )
            started = time.monotonic()
            planned_bundle = child_root / run_id
            try:
                outcome = execute_steady_run(
                    run_id=run_id,
                    blueprint=_blueprint(transient=False),
                    manifest=manifest,
                    recipe=recipe,
                    graph=graph,
                    mesh_policy=_mesh_policy(),
                    adapter=adapter,
                    bundle_service=bundle_service,
                    reference_kind=SteadyReferenceKind.ONE_DIMENSIONAL_CONDUCTION,
                )
                succeeded = outcome.execution_status is ExecutionStatus.SUCCEEDED
                children.append(
                    MeshStudyChild(
                        profile=profile,
                        run_id=run_id,
                        workdir=outcome.bundle_path,
                        execution_status=outcome.execution_status,
                        physical_verification_status=(
                            outcome.verification.status
                            if outcome.verification is not None
                            else VerificationStatus.NOT_RUN
                        ),
                        element_count=(_element_count(outcome.bundle_path) if succeeded else None),
                        runtime_s=time.monotonic() - started,
                        target_metric_value=(
                            _target_metric(outcome.bundle_path, criterion.target_metric)
                            if succeeded
                            else None
                        ),
                        message=outcome.message,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - preserve sibling runs on child failure
                children.append(
                    MeshStudyChild(
                        profile=profile,
                        run_id=run_id,
                        workdir=planned_bundle,
                        execution_status=ExecutionStatus.FAILED_SOLVER,
                        physical_verification_status=VerificationStatus.NOT_RUN,
                        runtime_s=time.monotonic() - started,
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
    finally:
        adapter.close()
    raw_children = tuple(children)
    persist_mesh_study_inputs(
        study_root,
        study_id=study_id,
        criterion=criterion,
        children=raw_children,
    )
    return aggregate_persisted_mesh_study(study_root)
