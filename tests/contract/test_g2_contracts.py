"""G2 schema, loading, compilation, and dependency-boundary contract tests."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ansys_research_runner.config import resource_path
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.recipe import (
    AnalysisSettings,
    MeshPolicyDocument,
    ModelManifest,
    RunRecipe,
)
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.domain.validation import validate_run_contracts
from ansys_research_runner.services.compilation_service import compile_cae_ir
from ansys_research_runner.services.contract_service import (
    deterministic_json,
    load_yaml_contract,
)
from ansys_research_runner.services.schema_service import (
    SCHEMA_MODELS,
    check_schemas,
    rendered_schemas,
)
from tests.g2_fixtures import (
    ROOT,
    box_graph,
    box_manifest,
    mesh_policy,
    steady_blueprint,
    steady_recipe,
)


def test_committed_blueprints_and_mesh_policy_load() -> None:
    steady = steady_blueprint()
    transient = load_yaml_contract(
        resource_path("blueprints", "solid_transient_thermal.v1.yaml"),
        AnalysisBlueprint,
    )
    policy = mesh_policy()
    assert steady.blueprint.id == "solid_steady_thermal"
    assert transient.blueprint.id == "solid_transient_thermal"
    assert policy.policies["coarse"].diagonal_divisor == 15
    assert policy.policies["fine"].diagonal_divisor == 40


def test_public_contracts_reject_raw_runtime_ids() -> None:
    manifest = box_manifest().model_dump(mode="python", by_alias=True)
    manifest["internal_runtime_id"] = "forbidden"
    with pytest.raises(ValidationError, match="Extra inputs"):
        ModelManifest.model_validate(manifest)
    recipe = steady_recipe().model_dump(mode="python", by_alias=True)
    recipe["run"]["face_id"] = 42
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunRecipe.model_validate(recipe)


def test_public_schemas_exclude_runtime_ids_but_cae_ir_allows_them() -> None:
    schemas = rendered_schemas()
    for name in (
        "analysis-blueprint.v1.schema.json",
        "model-manifest.v1.schema.json",
        "run-recipe.v1.schema.json",
    ):
        assert "internal_runtime_id" not in schemas[name]
    assert "internal_runtime_id" in schemas["resolved-cae-ir.v1.schema.json"]


def test_committed_json_schemas_are_complete_and_current() -> None:
    output = ROOT / "docs" / "schemas"
    assert check_schemas(output) == []
    assert {path.name for path in output.glob("*.json")} == set(SCHEMA_MODELS)
    for path in output.glob("*.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["$schema"].startswith("https://json-schema.org/")
        assert document["$id"].endswith(path.name)


def test_schema_rendering_is_deterministic() -> None:
    first = rendered_schemas()
    second = rendered_schemas()
    assert first == second
    assert list(first) == sorted(first)


def test_yaml_loader_is_safe_and_bounded(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.yaml"
    unsafe.write_text("!!python/object/apply:os.system ['echo unsafe']", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        load_yaml_contract(unsafe, ModelManifest)
    oversized = tmp_path / "large.yaml"
    oversized.write_text("x" * 32, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        load_yaml_contract(oversized, ModelManifest, maximum_bytes=16)


def test_recipe_requires_units_and_rejects_script_fields() -> None:
    payload = steady_recipe().model_dump(mode="json", by_alias=True)
    payload["materials"]["thermal_domain"]["thermal_conductivity"] = 15
    with pytest.raises(ValidationError, match="explicit unit"):
        RunRecipe.model_validate(payload)
    payload = steady_recipe().model_dump(mode="json", by_alias=True)
    payload["script"] = "import os"
    with pytest.raises(ValidationError, match="Extra inputs"):
        RunRecipe.model_validate(payload)


def test_compiler_produces_complete_resolved_cae_ir() -> None:
    graph = box_graph()
    manifest = box_manifest()
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id="run-g2-contract",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=steady_recipe(),
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy(),
        compiled_at="2026-08-23T00:00:00Z",
    )
    assert cae_ir.validation_summary.valid
    assert cae_ir.geometry.fingerprint == graph.fingerprint()
    assert cae_ir.resolved_bodies[0].internal_runtime_id == "runtime:box:body"
    assert {item.stable_key for item in cae_ir.resolved_faces} >= {
        "box.face.x_min",
        "box.face.x_max",
    }
    assert cae_ir.mesh_policy.characteristic_length.si_value == pytest.approx(
        graph.bodies[0].bounding_box.diagonal / 25.0
    )
    serialized = deterministic_json(cae_ir)
    assert ResolvedCAEIR.model_validate_json(serialized) == cae_ir
    assert deterministic_json(cae_ir) == serialized


def test_compiler_rejects_invalid_cross_contract_inputs() -> None:
    graph = box_graph()
    manifest = box_manifest()
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    invalid_payload = steady_recipe().model_dump(mode="json", by_alias=True)
    invalid_payload["run"]["blueprint"] = {
        "id": "solid_transient_thermal",
        "version": 1,
    }
    invalid = RunRecipe.model_validate(invalid_payload)
    with pytest.raises(DomainError) as failure:
        compile_cae_ir(
            run_id="invalid",
            blueprint=steady_blueprint(),
            manifest=manifest,
            recipe=invalid,
            graph=graph,
            resolution=resolution,
            mesh_policy=mesh_policy(),
        )
    assert failure.value.code is ErrorCode.PREFLIGHT_VALIDATION_FAILED
    assert failure.value.details["issues"][0]["code"] == "BLUEPRINT_MISMATCH"


def test_transient_blueprint_requires_density_and_specific_heat() -> None:
    transient = load_yaml_contract(
        resource_path("blueprints", "solid_transient_thermal.v1.yaml"),
        AnalysisBlueprint,
    )
    payload = steady_recipe().model_dump(mode="json", by_alias=True)
    payload["run"]["blueprint"] = "solid_transient_thermal@1"
    payload["analysis"] = {
        "type": "transient",
        "initial_temperature": "20 degC",
        "end_time": "10 s",
        "time_step": "1 s",
    }
    recipe = RunRecipe.model_validate(payload)
    graph = box_graph()
    manifest = box_manifest()
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    report = validate_run_contracts(
        transient,
        manifest.roles,
        recipe,
        graph,
        resolution,
    )
    missing_paths = {
        issue.path for issue in report.issues if issue.code == "MATERIAL_PROPERTY_MISSING"
    }
    assert missing_paths == {
        "materials.thermal_domain.density",
        "materials.thermal_domain.specific_heat",
    }


def test_manifest_recipe_and_graph_json_round_trip() -> None:
    for model in (box_manifest(), steady_recipe(), box_graph(), mesh_policy()):
        model_type = type(model)
        restored = model_type.model_validate_json(deterministic_json(model))
        assert restored == model


def test_domain_package_has_no_pyansys_imports() -> None:
    domain_root = ROOT / "src" / "ansys_research_runner" / "domain"
    forbidden = (
        "ansys.geometry",
        "ansys.mechanical",
        "ansys.dpf",
        "ansys.workbench",
        "ansys.meshing",
    )
    imported: list[str] = []
    for path in domain_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
    assert not [name for name in imported if name.startswith(forbidden)]


def test_mesh_policy_requires_every_supported_intent() -> None:
    with pytest.raises(ValidationError, match="must define"):
        MeshPolicyDocument.model_validate(
            {
                "schema_version": 1,
                "policy_id": "thermal_mesh_intent",
                "policies": {"coarse": {"diagonal_divisor": 15}},
            }
        )


def test_steady_analysis_rejects_transient_only_controls() -> None:
    with pytest.raises(ValidationError, match="transient initial"):
        AnalysisSettings.model_validate({"type": "steady", "initial_temperature": "20 degC"})
