"""G4 solver-neutral steady thermal vertical-slice tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.adapters.solver.base import (
    PostprocessResult,
    PreparedRun,
    RunCallbacks,
    SolverCapabilityReport,
    SolveResult,
)
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph, Vector3
from ansys_research_runner.domain.recipe import ModelManifest, RunRecipe
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    HotspotSummary,
    ProbeInterpolationStatus,
    ProbeResult,
    ResultQuality,
    ScalarResultSummary,
    TemperatureSummary,
    ThermalObservation,
    VerificationStatus,
)
from ansys_research_runner.domain.validation import ValidationReport
from ansys_research_runner.services.contract_service import deterministic_json
from ansys_research_runner.services.field_service import (
    TemperatureFieldData,
    mesh_sha256,
    write_temperature_field,
)
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.steady_run_service import (
    SteadyReferenceKind,
    execute_steady_run,
)
from tests.g2_fixtures import box_manifest, mesh_policy, steady_blueprint, steady_recipe


def _source_backed_graph(graph: GeometryGraph, source: Path) -> GeometryGraph:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return graph.model_copy(update={"source_path": str(source), "source_sha256": digest})


def _cylinder_manifest(source: Path) -> ModelManifest:
    return ModelManifest.model_validate(
        {
            "schema_version": 1,
            "model": {"file": str(source), "length_unit": "m"},
            "coordinate_frame": {
                "type": "explicit",
                "origin": [0, 0, 0],
                "x_axis": [1, 0, 0],
                "y_axis": [0, 1, 0],
                "z_axis": [0, 0, 1],
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


def _cylinder_recipe(manifest_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "steady_cylinder",
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
            "mesh": {"intent": "balanced"},
            "outputs": [
                {"type": "global_temperature_extrema"},
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


class ReferenceFakeSolver:
    """Deterministic test-only adapter; never exposed by the production package."""

    def probe_capabilities(self) -> SolverCapabilityReport:
        return SolverCapabilityReport(
            backend="reference_fake",
            available=True,
            package_version="test-only",
            product_version="test-only",
            launch_mode="in_process_test",
            capabilities=("steady_thermal", "temperature_field"),
        )

    def prepare(self, cae_ir: ResolvedCAEIR, workdir: Path) -> PreparedRun:
        workdir.mkdir(parents=True, exist_ok=True)
        output = workdir / "solver-output"
        output.mkdir(exist_ok=True)
        cae_ir_path = workdir / "cae_ir.json"
        cae_ir_path.write_text(deterministic_json(cae_ir), encoding="utf-8")
        return PreparedRun(
            run_id=cae_ir.run_id,
            workdir=workdir,
            cae_ir_path=cae_ir_path,
            source_model_path=Path(cae_ir.geometry.file),
            solver_output_dir=output,
        )

    def precheck(self, prepared: PreparedRun) -> ValidationReport:
        return ValidationReport(valid=prepared.source_model_path.is_file())

    def solve(self, prepared: PreparedRun, callbacks: RunCallbacks) -> SolveResult:
        callbacks.heartbeat()
        callbacks.log("reference fake solve")
        return SolveResult(
            run_id=prepared.run_id,
            status=ExecutionStatus.SUCCEEDED,
            converged=True,
            started_at="2026-08-23T00:00:00Z",
            finished_at="2026-08-23T00:00:01Z",
            exit_code=0,
            message="test-only reference result",
        )

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        del solve_result
        if "cylinder" in prepared.source_model_path.name:
            generation = np.pi * 0.5**2 * 2.0 * 1000.0
            observation = ThermalObservation(
                summary=ScalarResultSummary(
                    temperature=TemperatureSummary(
                        minimum_K=310.0,
                        maximum_K=350.0,
                        volume_average_K=330.0,
                    ),
                    hotspot=HotspotSummary(position_m=Vector3((0, 0, 0)), value_K=350.0),
                ),
                heat_generation_W=generation,
                heat_rejection_W=generation,
            )
            temperatures = np.array([[310.0, 330.0, 350.0]], dtype=np.float64)
        else:
            center = ProbeResult(
                name="center",
                requested_position_m=Vector3((0.5, 0.5, 0.5)),
                coordinate_system="normalized_model_coordinates",
                mapped_position_m=Vector3((0, 0, 0)),
                inside_mesh=True,
                interpolation_status=ProbeInterpolationStatus.INTERPOLATED,
                value_K=333.15,
            )
            observation = ThermalObservation(
                summary=ScalarResultSummary(
                    temperature=TemperatureSummary(
                        minimum_K=293.15,
                        maximum_K=373.15,
                        volume_average_K=333.15,
                    ),
                    hotspot=HotspotSummary(position_m=Vector3((0.5, 0, 0)), value_K=373.15),
                ),
                probes=(center,),
                boundary_temperatures_K={
                    "cold_boundary": 293.15,
                    "hot_boundary": 373.15,
                },
                heat_input_W=7200.0,
                heat_output_W=7200.0,
            )
            temperatures = np.array([[293.15, 333.15, 373.15]], dtype=np.float64)
        node_ids = np.array([30, 10, 20], dtype=np.int64)
        coordinates = np.array([[0, 0, 0], [0.5, 0, 0], [1, 0, 0]], dtype=np.float64)
        element_ids = np.array([7], dtype=np.int64)
        connectivity = np.array([[30, 10, 20]], dtype=np.int64)
        digest = mesh_sha256(node_ids, coordinates, element_ids, connectivity)
        field_path = prepared.solver_output_dir / "temperature_field.h5"
        report = write_temperature_field(
            field_path,
            TemperatureFieldData(
                node_ids=node_ids,
                coordinates_m=coordinates,
                element_ids=element_ids,
                connectivity=connectivity,
                times_s=np.array([0.0], dtype=np.float64),
                temperature_K=temperatures,
                mesh_sha256=digest,
            ),
        )
        assert report.valid
        return PostprocessResult(
            observation=observation,
            field_path=field_path,
            mesh_sha256=digest,
        )

    def request_cancel(self, prepared: PreparedRun) -> None:
        del prepared

    def close(self) -> None:
        pass


@pytest.mark.parametrize("case", ["box", "cylinder"])
def test_two_geometries_share_blueprint_compiler_runner_and_bundle(
    tmp_path: Path,
    case: str,
) -> None:
    source = tmp_path / f"{case}.step"
    source.write_bytes(f"non-proprietary-{case}".encode())
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if case == "box":
        graph = _source_backed_graph(synthetic_graph(GeometrySpec(kind=GeometryKind.BOX)), source)
        manifest = box_manifest().model_copy(
            update={"model": box_manifest().model.model_copy(update={"file": str(source)})}
        )
        recipe = steady_recipe().model_copy(
            update={
                "run": steady_recipe().run.model_copy(
                    update={"model_manifest": str(tmp_path / "box.manifest.yaml")}
                )
            }
        )
        reference = SteadyReferenceKind.ONE_DIMENSIONAL_CONDUCTION
    else:
        graph = _source_backed_graph(
            synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER)), source
        )
        manifest = _cylinder_manifest(source)
        recipe = _cylinder_recipe(tmp_path / "cylinder.manifest.yaml")
        reference = SteadyReferenceKind.UNIFORM_GENERATION_CONVECTION
    outcome = execute_steady_run(
        run_id=f"g4-{case}",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=recipe,
        graph=graph,
        mesh_policy=mesh_policy(),
        adapter=ReferenceFakeSolver(),
        bundle_service=RunBundleService(tmp_path / "runs"),
        reference_kind=reference,
    )
    assert outcome.execution_status is ExecutionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.status is VerificationStatus.PASSED
    assert outcome.quality.result_quality is ResultQuality.PHYSICALLY_VERIFIED
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    required = (
        "request/recipe.yaml",
        "request/model_manifest.yaml",
        "request/input_hashes.json",
        "resolved/cae_ir.json",
        "resolved/geometry_graph.json",
        "resolved/region_resolution.json",
        "resolved/validation_pre.json",
        "results/summary.json",
        "results/probes.csv",
        "results/temperature_field.h5",
        "results/validation_post.json",
        "state.json",
        "manifest.json",
    )
    assert all((outcome.bundle_path / name).is_file() for name in required)
    cae_ir = ResolvedCAEIR.model_validate_json(
        (outcome.bundle_path / "resolved/cae_ir.json").read_text(encoding="utf-8")
    )
    assert cae_ir.blueprint_id == "solid_steady_thermal"


def test_outside_mesh_probe_cannot_contain_a_fabricated_temperature() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        ProbeResult(
            name="outside",
            requested_position_m=Vector3((2, 0, 0)),
            coordinate_system="model",
            inside_mesh=False,
            interpolation_status=ProbeInterpolationStatus.OUTSIDE_MESH,
            value_K=0.0,
        )
