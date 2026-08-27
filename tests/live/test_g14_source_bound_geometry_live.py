"""Live Ansys 2026 R1 qualification for the source-bound exact Geometry tier."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from ansys_research_runner.adapters.geometry.base import GeometryInspectionRequest
from ansys_research_runner.adapters.geometry.base import TestGeometryKind as GeometryKind
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.pyansys_geometry import PyAnsysGeometryAdapter
from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.errors import ErrorCode
from ansys_research_runner.domain.geometry import GeometryCapabilityTier
from ansys_research_runner.domain.jobs import JobStatus
from ansys_research_runner.domain.selectors import (
    ResolutionStatus,
    RoleDefinition,
    resolve_regions,
)
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.application_service import ResearchRunnerApplication
from ansys_research_runner.services.production_worker_service import run_production_worker_once

ROOT = Path(__file__).resolve().parents[2]


def _role(entity: str, cardinality: str, selector: dict[str, object]) -> RoleDefinition:
    return RoleDefinition.model_validate(
        {"entity": entity, "cardinality": cardinality, "selector": selector}
    )


@pytest.mark.ansys_live
def test_g14_source_bound_named_selections_and_step_are_repeatable(tmp_path: Path) -> None:
    adapter = PyAnsysGeometryAdapter(inspect_timeout_seconds=240.0)
    report = adapter.probe_capabilities()
    assert report.available
    assert report.reason == "SOURCE_BOUND_EXACT_ONLY"

    fixture = adapter.generate_test_asset(
        GeometrySpec(kind=GeometryKind.BOX, dimensions_m=(1.0, 2.0, 3.0)),
        tmp_path,
    )
    named_first = adapter.inspect(GeometryInspectionRequest(model_path=fixture))
    named_second = adapter.inspect(GeometryInspectionRequest(model_path=fixture))

    assert named_first.capability_tier is GeometryCapabilityTier.SOURCE_BOUND_EXACT
    assert named_first.fingerprint() == named_second.fingerprint()
    assert {body.stable_key for body in named_first.bodies} == {
        body.stable_key for body in named_second.bodies
    }
    assert {face.stable_key for face in named_first.faces} == {
        face.stable_key for face in named_second.faces
    }
    assert all(face.centroid is None for face in named_first.faces)

    named_roles = {
        "domain": _role("body", "exactly_one", {"named_selection": "THERMAL_DOMAIN"}),
        "cold": _role("face", "exactly_one", {"named_selection": "COLD_FACE"}),
        "hot": _role("face", "exactly_one", {"named_selection": "HOT_FACE"}),
        "exterior": _role("face", "one_or_more", {"named_selection": "EXTERIOR"}),
    }
    named_resolution = resolve_regions(named_first, named_roles)
    assert named_resolution.successful
    assert named_resolution.roles["exterior"].selected_count == 6

    step = resource_path("geometry", "g3_box.step").resolve(strict=True)
    step_first = adapter.inspect(GeometryInspectionRequest(model_path=step))
    step_second = adapter.inspect(GeometryInspectionRequest(model_path=step))
    assert step_first.fingerprint() == step_second.fingerprint()

    geometric_roles = {
        "domain": _role("body", "exactly_one", {"solid_body": True}),
        "cold": _role(
            "face",
            "exactly_one",
            {
                "all": [
                    {"normal_parallel_to": {"axis": "local_x", "tolerance_deg": 1}},
                    {"bounding_box_extreme": {"axis": "x", "side": "minimum"}},
                ]
            },
        ),
        "hot": _role(
            "face",
            "exactly_one",
            {
                "all": [
                    {"normal_parallel_to": {"axis": "local_x", "tolerance_deg": 1}},
                    {"bounding_box_extreme": {"axis": "x", "side": "maximum"}},
                ]
            },
        ),
        "exterior": _role("face", "one_or_more", {"external_of": "domain"}),
    }
    geometric_resolution = resolve_regions(step_first, geometric_roles)
    assert geometric_resolution.successful
    assert (
        geometric_resolution.roles["cold"].candidate_keys
        != geometric_resolution.roles["hot"].candidate_keys
    )

    unsupported = resolve_regions(
        step_first,
        {
            "centroid_face": _role(
                "face",
                "exactly_one",
                {"centroid_extreme": {"axis": "local_x", "side": "maximum"}},
            )
        },
    ).roles["centroid_face"]
    assert unsupported.status is ResolutionStatus.UNSUPPORTED
    assert unsupported.error is not None
    assert unsupported.error.code is ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY

    run_id = f"g14-source-bound-{uuid.uuid4().hex[:12]}"
    qualification_runtime = (ROOT / "runtime" / "g14-qualification" / run_id).resolve()
    paths = RunnerPaths(
        root=ROOT,
        runtime=qualification_runtime,
        runs=qualification_runtime / "runs",
        gates=qualification_runtime / "gates",
        blockers=qualification_runtime / "blockers",
        database=qualification_runtime / "jobs.sqlite",
    )
    application = ResearchRunnerApplication(paths=paths)
    recipe = resource_path("geometry", "source-bound-steady-conduction.recipe.yaml")
    queued = application.run(recipe, run_id=run_id)
    assert queued.job.status is JobStatus.QUEUED
    terminal = run_production_worker_once(
        paths=paths,
        worker_id=f"g14-live-{uuid.uuid4().hex[:8]}",
    )
    assert terminal is not None
    assert terminal.job_id == run_id
    assert terminal.status is JobStatus.SUCCEEDED

    results = application.results(run_id)
    assert results.summary is not None
    artifacts = application.artifacts(run_id).artifacts
    artifact_paths = {item.path for item in artifacts}
    required_suffixes = (
        "summary.json",
        "temperature-field.h5",
        "solver-result.rst",
        "solver-cdb.cdb",
    )
    assert all(
        any(path.endswith(suffix) for path in artifact_paths) for suffix in required_suffixes
    )

    atomic_write_json(
        ROOT / "runtime" / "g14_source_bound_geometry_report.json",
        {
            "schema_version": 1,
            "status": "PASSED",
            "capability_tier": GeometryCapabilityTier.SOURCE_BOUND_EXACT.value,
            "spaceclaim_backend": report.backend,
            "named_selection_fingerprint_repeatable": True,
            "step_fingerprint_repeatable": True,
            "centroid_selector_fail_closed": True,
            "terminal_status": terminal.status.value,
            "run_id": run_id,
            "run_root": str(paths.runs / run_id),
            "required_artifacts_present": True,
            "temperature_summary": results.summary,
        },
    )

    adapter.close()
