"""Failure-side tests for independent steady thermal verification."""

from __future__ import annotations

from pathlib import Path

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.domain.geometry import Vector3
from ansys_research_runner.domain.results import (
    ProbeInterpolationStatus,
    ProbeResult,
    ScalarResultSummary,
    TemperatureSummary,
    ThermalObservation,
    VerificationStatus,
)
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.services.compilation_service import compile_cae_ir
from ansys_research_runner.services.thermal_verification_service import (
    verify_steady_conduction,
    verify_uniform_generation_convection,
)
from tests.g2_fixtures import box_graph, box_manifest, mesh_policy, steady_blueprint, steady_recipe
from tests.unit.test_g4_steady_core import _cylinder_manifest, _cylinder_recipe


def test_conduction_reference_fails_a_center_temperature_outside_tolerance() -> None:
    graph = box_graph()
    manifest = box_manifest()
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id="bad-center",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=steady_recipe(),
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy(),
    )
    observation = ThermalObservation(
        summary=ScalarResultSummary(
            temperature=TemperatureSummary(
                minimum_K=293.15,
                maximum_K=373.15,
                volume_average_K=340.0,
            )
        ),
        probes=(
            ProbeResult(
                name="center",
                requested_position_m=Vector3((0.5, 0.5, 0.5)),
                coordinate_system="normalized_model_coordinates",
                mapped_position_m=Vector3((0, 0, 0)),
                inside_mesh=True,
                interpolation_status=ProbeInterpolationStatus.INTERPOLATED,
                value_K=340.0,
            ),
        ),
        boundary_temperatures_K={"cold_boundary": 293.15, "hot_boundary": 373.15},
        heat_input_W=7200.0,
        heat_output_W=7200.0,
    )
    report = verify_steady_conduction(cae_ir=cae_ir, graph=graph, observation=observation)
    assert report.status is VerificationStatus.FAILED
    failed = {
        metric.name for metric in report.metrics if metric.status is VerificationStatus.FAILED
    }
    assert failed == {"center_temperature"}


def test_generation_reference_fails_nonphysical_temperature_ordering(tmp_path: Path) -> None:
    source = tmp_path / "cylinder.step"
    source.write_bytes(b"cylinder")
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER))
    manifest = _cylinder_manifest(source)
    recipe = _cylinder_recipe(tmp_path / "manifest.yaml")
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id="bad-order",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=recipe,
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy(),
    )
    generation = 1000.0 * graph.bodies[0].volume.si_value
    observation = ThermalObservation(
        summary=ScalarResultSummary(
            temperature=TemperatureSummary(
                minimum_K=280.0,
                maximum_K=300.0,
                volume_average_K=290.0,
            )
        ),
        heat_generation_W=generation,
        heat_rejection_W=generation,
    )
    report = verify_uniform_generation_convection(
        cae_ir=cae_ir,
        graph=graph,
        observation=observation,
    )
    assert report.status is VerificationStatus.FAILED
    assert report.checks["temperature_ordering"] is VerificationStatus.FAILED
