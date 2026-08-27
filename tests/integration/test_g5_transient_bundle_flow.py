"""Filesystem integration smoke for resolved transient profile provenance."""

from __future__ import annotations

import json
from pathlib import Path

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.transient_run_service import (
    TransientReferenceKind,
    execute_transient_run,
)
from tests.g2_fixtures import mesh_policy
from tests.unit.test_g4_steady_core import _cylinder_manifest, _source_backed_graph
from tests.unit.test_g5_transient_core import (
    TransientReferenceFakeSolver,
    _profile_recipe,
    _transient_blueprint,
)


def test_g5_profile_is_copied_hashed_and_embedded_in_cae_ir(tmp_path: Path) -> None:
    source = tmp_path / "cylinder.step"
    source.write_bytes(b"integration-cylinder")
    profile = tmp_path / "profile.csv"
    profile.write_text(
        "time_s,heat_generation_W_m3\n0,0\n5,1000\n10,500\n",
        encoding="utf-8",
    )
    graph = _source_backed_graph(synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER)), source)
    outcome = execute_transient_run(
        run_id="g5-integration-profile",
        blueprint=_transient_blueprint(),
        manifest=_cylinder_manifest(source),
        recipe=_profile_recipe(tmp_path / "manifest.yaml", profile.name),
        graph=graph,
        mesh_policy=mesh_policy(),
        adapter=TransientReferenceFakeSolver(),
        bundle_service=RunBundleService(tmp_path / "runs"),
        reference_kind=TransientReferenceKind.TIME_SERIES_PROFILE,
        recipe_base_dir=tmp_path,
        allowed_input_root=tmp_path,
    )
    assert outcome.execution_status is ExecutionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.status is VerificationStatus.PASSED
    cae_ir = ResolvedCAEIR.model_validate_json(
        (outcome.bundle_path / "resolved/cae_ir.json").read_text(encoding="utf-8")
    )
    assert cae_ir.resolved_time_profiles["thermal_domain"].points[-1].time_s == 10.0
    input_hashes = json.loads(
        (outcome.bundle_path / "request/input_hashes.json").read_text(encoding="utf-8")
    )
    assert "heat_generation_profile_0.csv" in input_hashes
