"""Filesystem integration smoke for the G4 vertical-slice service."""

from __future__ import annotations

import json
from pathlib import Path

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.steady_run_service import (
    SteadyReferenceKind,
    execute_steady_run,
)
from tests.g2_fixtures import box_manifest, mesh_policy, steady_blueprint, steady_recipe
from tests.unit.test_g4_steady_core import (
    ReferenceFakeSolver,
    _source_backed_graph,
)


def test_g4_bundle_manifest_hashes_existing_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "box.step"
    source.write_bytes(b"integration-box")
    graph = _source_backed_graph(synthetic_graph(GeometrySpec(kind=GeometryKind.BOX)), source)
    manifest = box_manifest().model_copy(
        update={"model": box_manifest().model.model_copy(update={"file": str(source)})}
    )
    outcome = execute_steady_run(
        run_id="g4-integration-box",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=steady_recipe(),
        graph=graph,
        mesh_policy=mesh_policy(),
        adapter=ReferenceFakeSolver(),
        bundle_service=RunBundleService(tmp_path / "runs"),
        reference_kind=SteadyReferenceKind.ONE_DIMENSIONAL_CONDUCTION,
    )
    bundle_manifest = json.loads(
        (outcome.bundle_path / "manifest.json").read_text(encoding="utf-8")
    )
    assert outcome.execution_status is ExecutionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.status is VerificationStatus.PASSED
    assert {item["path"] for item in bundle_manifest["artifacts"]} >= {
        "request/recipe.yaml",
        "resolved/cae_ir.json",
        "results/summary.json",
        "results/temperature_field.h5",
    }
