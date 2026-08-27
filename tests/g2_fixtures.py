"""Reusable G2 contract fixtures."""

from __future__ import annotations

from pathlib import Path

from ansys_research_runner.adapters.geometry.base import TestGeometryKind, TestGeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.config import resource_path
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import MeshPolicyDocument, ModelManifest, RunRecipe
from ansys_research_runner.services.contract_service import load_yaml_contract

ROOT = Path(__file__).resolve().parents[1]


def box_graph() -> GeometryGraph:
    """Return the standard asymmetric box graph."""

    return synthetic_graph(TestGeometrySpec(kind=TestGeometryKind.BOX))


def steady_blueprint() -> AnalysisBlueprint:
    """Load the committed steady thermal Blueprint."""

    return load_yaml_contract(
        resource_path("blueprints", "solid_steady_thermal.v1.yaml"),
        AnalysisBlueprint,
    )


def mesh_policy() -> MeshPolicyDocument:
    """Load the committed mesh policy."""

    return load_yaml_contract(resource_path("policies", "mesh.v1.yaml"), MeshPolicyDocument)


def box_manifest() -> ModelManifest:
    """Return a manifest selecting the box body and its local X end faces."""

    return ModelManifest.model_validate(
        {
            "schema_version": 1,
            "model": {"file": "box.step", "length_unit": "m"},
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
                            {
                                "centroid_extreme": {
                                    "axis": "local_x",
                                    "side": "maximum",
                                }
                            },
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
                            {
                                "centroid_extreme": {
                                    "axis": "local_x",
                                    "side": "minimum",
                                }
                            },
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


def steady_recipe() -> RunRecipe:
    """Return a valid steady box recipe."""

    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "steady_box",
                "blueprint": "solid_steady_thermal@1",
                "model_manifest": "box.manifest.yaml",
            },
            "materials": {"thermal_domain": {"thermal_conductivity": "15 W/m/K"}},
            "boundary_conditions": [
                {"type": "temperature", "region": "cold_boundary", "value": "20 degC"},
                {"type": "temperature", "region": "hot_boundary", "value": "100 degC"},
            ],
            "mesh": {"intent": "balanced", "maximum_elements": 300000},
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
