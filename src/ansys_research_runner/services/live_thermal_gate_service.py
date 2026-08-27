"""Actual four-case thermal Gate execution through the production adapter."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from ansys_research_runner.adapters.geometry.base import TestGeometryKind, TestGeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph, transform_graph
from ansys_research_runner.adapters.solver.mapdl import MapdlSolverAdapter
from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.geometry import GeometryGraph, Vector3
from ansys_research_runner.domain.recipe import MeshPolicyDocument, ModelManifest, RunRecipe
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus
from ansys_research_runner.io import atomic_write_text
from ansys_research_runner.services.contract_service import load_yaml_contract
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.steady_run_service import (
    SteadyReferenceKind,
    SteadyRunOutcome,
    execute_steady_run,
)
from ansys_research_runner.services.transient_run_service import (
    TransientReferenceKind,
    TransientRunOutcome,
    execute_transient_run,
)


def _source_graph(kind: TestGeometryKind, source: Path) -> GeometryGraph:
    if kind is TestGeometryKind.BOX:
        base = synthetic_graph(TestGeometrySpec(kind=kind, dimensions_m=(2.0, 3.0, 4.0)))
        graph = transform_graph(base, translation=Vector3((0.0, 0.0, 2.0)))
    else:
        graph = synthetic_graph(TestGeometrySpec(kind=kind, radius_m=0.5, length_m=2.0))
    return graph.model_copy(
        update={
            "source_path": str(source.resolve()),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "metadata": {
                "gate_reference": "analytic descriptor for committed CAD fixture",
                "solver_geometry_source": str(source.resolve()),
            },
        }
    )


def _blueprint(transient: bool) -> AnalysisBlueprint:
    name = "solid_transient_thermal.v1.yaml" if transient else "solid_steady_thermal.v1.yaml"
    return load_yaml_contract(resource_path("blueprints", name), AnalysisBlueprint)


def _mesh_policy() -> MeshPolicyDocument:
    return load_yaml_contract(resource_path("policies", "mesh.v1.yaml"), MeshPolicyDocument)


def _box_manifest(source: Path) -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "schema_version": 1,
            "model": {"file": str(source.resolve()), "length_unit": "m"},
            "coordinate_frame": {
                "type": "explicit",
                "origin": [0.0, 0.0, 0.0],
                "x_axis": [1.0, 0.0, 0.0],
                "y_axis": [0.0, 1.0, 0.0],
                "z_axis": [0.0, 0.0, 1.0],
            },
            "roles": {
                "thermal_domain": {
                    "entity": "body",
                    "cardinality": "one_or_more",
                    "selector": {"solid_body": True},
                },
                "hot_boundary": {
                    "entity": "face",
                    "cardinality": "exactly_one",
                    "selector": {
                        "all": [
                            {"parent_body_role": "thermal_domain"},
                            {"surface_type": "planar"},
                            {"centroid_extreme": {"axis": "local_x", "side": "maximum"}},
                            {
                                "normal_parallel_to": {
                                    "axis": "local_x",
                                    "tolerance_deg": 5,
                                }
                            },
                        ]
                    },
                },
                "cold_boundary": {
                    "entity": "face",
                    "cardinality": "exactly_one",
                    "selector": {
                        "all": [
                            {"parent_body_role": "thermal_domain"},
                            {"surface_type": "planar"},
                            {"centroid_extreme": {"axis": "local_x", "side": "minimum"}},
                            {
                                "normal_parallel_to": {
                                    "axis": "local_x",
                                    "tolerance_deg": 5,
                                }
                            },
                        ]
                    },
                },
                "exterior": {
                    "entity": "face",
                    "cardinality": "one_or_more",
                    "selector": {
                        "all": [
                            {"external_of": "thermal_domain"},
                            {"interface": False},
                        ]
                    },
                },
            },
        }
    )


def _cylinder_manifest(source: Path) -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "schema_version": 1,
            "model": {"file": str(source.resolve()), "length_unit": "m"},
            "coordinate_frame": {
                "type": "explicit",
                "origin": [0.0, 0.0, 0.0],
                "x_axis": [1.0, 0.0, 0.0],
                "y_axis": [0.0, 1.0, 0.0],
                "z_axis": [0.0, 0.0, 1.0],
            },
            "roles": {
                "thermal_domain": {
                    "entity": "body",
                    "cardinality": "one_or_more",
                    "selector": {"solid_body": True},
                },
                "exterior": {
                    "entity": "face",
                    "cardinality": "one_or_more",
                    "selector": {
                        "all": [
                            {"external_of": "thermal_domain"},
                            {"interface": False},
                        ]
                    },
                },
            },
        }
    )


def _steady_box_recipe(manifest_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "steady_conduction_box",
                "blueprint": "solid_steady_thermal@1",
                "model_manifest": str(manifest_path),
            },
            "materials": {"thermal_domain": {"thermal_conductivity": "15 W/m/K"}},
            "boundary_conditions": [
                {"type": "temperature", "region": "cold_boundary", "value": "20 degC"},
                {"type": "temperature", "region": "hot_boundary", "value": "100 degC"},
            ],
            "mesh": {"intent": "balanced", "maximum_elements": 100000},
            "outputs": [
                {"type": "global_temperature_extrema"},
                {
                    "type": "coordinate_probe",
                    "name": "center",
                    "point": [0.5, 0.5, 0.5],
                    "point_unit": "normalized_model_coordinates",
                },
                {"type": "temperature_field"},
            ],
        }
    )


def _steady_cylinder_recipe(manifest_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "generation_convection_cylinder",
                "blueprint": "solid_steady_thermal@1",
                "model_manifest": str(manifest_path),
            },
            "materials": {"thermal_domain": {"thermal_conductivity": "15 W/m/K"}},
            "boundary_conditions": [
                {
                    "type": "volumetric_heat_generation",
                    "region": "thermal_domain",
                    "value": "1000 W/m^3",
                },
                {
                    "type": "convection",
                    "region": "exterior",
                    "film_coefficient": "10 W/m^2/K",
                    "ambient_temperature": "20 degC",
                },
            ],
            "mesh": {"intent": "balanced", "maximum_size": "0.12 m"},
            "outputs": [
                {"type": "global_temperature_extrema"},
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


def _lumped_recipe(manifest_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "lumped_capacitance_reference",
                "blueprint": "solid_transient_thermal@1",
                "model_manifest": str(manifest_path),
            },
            "materials": {
                "thermal_domain": {
                    "thermal_conductivity": "200 W/m/K",
                    "density": "7800 kg/m^3",
                    "specific_heat": "500 J/kg/K",
                }
            },
            "boundary_conditions": [
                {
                    "type": "convection",
                    "region": "exterior",
                    "film_coefficient": "10 W/m^2/K",
                    "ambient_temperature": "20 degC",
                }
            ],
            "analysis": {
                "type": "transient",
                "initial_temperature": "100 degC",
                "end_time": "1000 s",
                "time_step": "250 s",
            },
            "mesh": {"intent": "balanced", "maximum_size": "0.6 m"},
            "outputs": [
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


def _profile_recipe(manifest_path: Path, profile_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "time_series_heat_generation",
                "blueprint": "solid_transient_thermal@1",
                "model_manifest": str(manifest_path),
            },
            "materials": {
                "thermal_domain": {
                    "thermal_conductivity": "15 W/m/K",
                    "density": "7800 kg/m^3",
                    "specific_heat": "500 J/kg/K",
                }
            },
            "boundary_conditions": [
                {
                    "type": "volumetric_heat_generation_profile",
                    "region": "thermal_domain",
                    "profile_file": profile_path.name,
                },
                {
                    "type": "convection",
                    "region": "exterior",
                    "film_coefficient": "10 W/m^2/K",
                    "ambient_temperature": "20 degC",
                },
            ],
            "analysis": {
                "type": "transient",
                "initial_temperature": "20 degC",
                "end_time": "10 s",
                "time_step": "2.5 s",
            },
            "mesh": {"intent": "balanced", "maximum_size": "0.12 m"},
            "outputs": [
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


def _case_payload(outcome: SteadyRunOutcome | TransientRunOutcome) -> dict[str, object]:
    execution = outcome.execution_status
    verification = outcome.verification
    quality = outcome.quality
    return {
        "execution_status": execution.value,
        "verification_status": None if verification is None else verification.status.value,
        "bundle_path": str(outcome.bundle_path),
        "result_quality": quality.result_quality.value,
    }


def run_actual_steady_gate_cases(
    *, probe_timeout_seconds: float = 180.0
) -> dict[str, dict[str, object]]:
    """Run the two required G4 cases through one production adapter code path."""

    paths = RunnerPaths.from_environment()
    box_source = resource_path("geometry", "g3_box.step")
    cylinder_source = resource_path("geometry", "g3_cylinder.step")
    run_root = paths.runtime / "live-runs" / "G4"
    bundle_service = RunBundleService(run_root)
    adapter = MapdlSolverAdapter(
        prime_timeout_s=probe_timeout_seconds,
        solve_timeout_s=probe_timeout_seconds,
    )
    suffix = uuid.uuid4().hex[:12]
    try:
        box = execute_steady_run(
            run_id=f"g4-box-{suffix}",
            blueprint=_blueprint(transient=False),
            manifest=_box_manifest(box_source),
            recipe=_steady_box_recipe(paths.runtime / "gate-inputs" / "box.manifest.yaml"),
            graph=_source_graph(TestGeometryKind.BOX, box_source),
            mesh_policy=_mesh_policy(),
            adapter=adapter,
            bundle_service=bundle_service,
            reference_kind=SteadyReferenceKind.ONE_DIMENSIONAL_CONDUCTION,
        )
        cylinder = execute_steady_run(
            run_id=f"g4-cylinder-{suffix}",
            blueprint=_blueprint(transient=False),
            manifest=_cylinder_manifest(cylinder_source),
            recipe=_steady_cylinder_recipe(
                paths.runtime / "gate-inputs" / "cylinder.manifest.yaml"
            ),
            graph=_source_graph(TestGeometryKind.CYLINDER, cylinder_source),
            mesh_policy=_mesh_policy(),
            adapter=adapter,
            bundle_service=bundle_service,
            reference_kind=SteadyReferenceKind.UNIFORM_GENERATION_CONVECTION,
        )
    finally:
        adapter.close()
    return {
        "steady_conduction_box": _case_payload(box),
        "generation_convection_cylinder": _case_payload(cylinder),
    }


def run_actual_transient_gate_cases(
    *, probe_timeout_seconds: float = 180.0
) -> dict[str, dict[str, object]]:
    """Run the two required G5 cases through the same production adapter path."""

    paths = RunnerPaths.from_environment()
    inputs = paths.runtime / "gate-inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    profile = inputs / "g5_heat_profile.csv"
    atomic_write_text(
        profile,
        "time_s,heat_generation_W_m3\n0,0\n5,1000\n10,500\n",
    )
    box_source = resource_path("geometry", "g3_box.step")
    cylinder_source = resource_path("geometry", "g3_cylinder.step")
    bundle_service = RunBundleService(paths.runtime / "live-runs" / "G5")
    adapter = MapdlSolverAdapter(
        prime_timeout_s=probe_timeout_seconds,
        solve_timeout_s=probe_timeout_seconds,
    )
    suffix = uuid.uuid4().hex[:12]
    try:
        lumped = execute_transient_run(
            run_id=f"g5-lumped-{suffix}",
            blueprint=_blueprint(transient=True),
            manifest=_box_manifest(box_source),
            recipe=_lumped_recipe(inputs / "box.manifest.yaml"),
            graph=_source_graph(TestGeometryKind.BOX, box_source),
            mesh_policy=_mesh_policy(),
            adapter=adapter,
            bundle_service=bundle_service,
            reference_kind=TransientReferenceKind.LUMPED_CAPACITANCE,
            recipe_base_dir=inputs,
            allowed_input_root=inputs,
        )
        profiled = execute_transient_run(
            run_id=f"g5-profile-{suffix}",
            blueprint=_blueprint(transient=True),
            manifest=_cylinder_manifest(cylinder_source),
            recipe=_profile_recipe(inputs / "cylinder.manifest.yaml", profile),
            graph=_source_graph(TestGeometryKind.CYLINDER, cylinder_source),
            mesh_policy=_mesh_policy(),
            adapter=adapter,
            bundle_service=bundle_service,
            reference_kind=TransientReferenceKind.TIME_SERIES_PROFILE,
            recipe_base_dir=inputs,
            allowed_input_root=inputs,
        )
    finally:
        adapter.close()
    return {
        "lumped_capacitance_reference": _case_payload(lumped),
        "time_series_heat_generation": _case_payload(profiled),
    }


def successful_case_names(cases: dict[str, dict[str, object]]) -> tuple[str, ...]:
    """Return cases with distinct solver and physical success evidence."""

    return tuple(
        name
        for name, evidence in cases.items()
        if evidence.get("execution_status") == ExecutionStatus.SUCCEEDED.value
        and evidence.get("verification_status") == VerificationStatus.PASSED.value
    )
