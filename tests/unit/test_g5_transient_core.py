"""G5 transient thermal reference, profile, and common-runner tests."""

from __future__ import annotations

import hashlib
import json
import math
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
from ansys_research_runner.config import resource_path
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import RunRecipe
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    ResultQuality,
    ScalarResultSummary,
    TemperatureSummary,
    TransientThermalObservation,
    VerificationStatus,
)
from ansys_research_runner.domain.validation import ValidationReport
from ansys_research_runner.services.contract_service import deterministic_json, load_yaml_contract
from ansys_research_runner.services.field_service import (
    TemperatureFieldData,
    mesh_sha256,
    validate_temperature_field,
    write_temperature_field,
)
from ansys_research_runner.services.run_bundle_service import RunBundleService
from ansys_research_runner.services.transient_profile_service import load_heat_generation_profile
from ansys_research_runner.services.transient_run_service import (
    TransientReferenceKind,
    execute_transient_run,
)
from tests.g2_fixtures import box_manifest, mesh_policy
from tests.unit.test_g4_steady_core import _cylinder_manifest, _source_backed_graph


def _transient_blueprint() -> AnalysisBlueprint:
    return load_yaml_contract(
        resource_path("blueprints", "solid_transient_thermal.v1.yaml"),
        AnalysisBlueprint,
    )


def _lumped_recipe(manifest_path: Path) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "transient_lumped_box",
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
            "outputs": [
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


def _profile_recipe(manifest_path: Path, profile_name: str) -> RunRecipe:
    return RunRecipe.model_validate(
        {
            "schema_version": 1,
            "run": {
                "case_id": "transient_profile_cylinder",
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
                    "profile_file": profile_name,
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
            "outputs": [
                {"type": "body_average_temperature", "region": "thermal_domain"},
                {"type": "temperature_field"},
            ],
        }
    )


class TransientReferenceFakeSolver:
    """Test-only transient adapter emitting analytic or profile-aligned observations."""

    def probe_capabilities(self) -> SolverCapabilityReport:
        return SolverCapabilityReport(
            backend="transient_reference_fake",
            available=True,
            package_version="test-only",
            product_version="test-only",
            capabilities=("transient_thermal", "temperature_field"),
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
        return SolveResult(
            run_id=prepared.run_id,
            status=ExecutionStatus.SUCCEEDED,
            converged=True,
            started_at="2026-08-23T00:00:00Z",
            finished_at="2026-08-23T00:00:01Z",
            exit_code=0,
            message="test-only transient reference",
        )

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        del solve_result
        cae_ir = ResolvedCAEIR.model_validate_json(prepared.cae_ir_path.read_text(encoding="utf-8"))
        if "lumped" in prepared.run_id:
            times = np.array([0, 250, 500, 750, 1000], dtype=np.float64)
            material = cae_ir.materials["thermal_domain"]
            convection = cae_ir.boundary_conditions[0]
            assert material.density is not None and material.specific_heat is not None
            assert hasattr(convection, "film_coefficient")
            decay = 10.0 * 22.0 / (7800.0 * 500.0 * 6.0)
            averages = np.array(
                [293.15 + 80.0 * math.exp(-decay * value) for value in times],
                dtype=np.float64,
            )
        else:
            times = np.array([0, 2.5, 5, 7.5, 10], dtype=np.float64)
            averages = np.array([293.15, 293.2, 293.3, 293.45, 293.6], dtype=np.float64)
        observation = TransientThermalObservation(
            summary=ScalarResultSummary(
                temperature=TemperatureSummary(
                    minimum_K=float(np.min(averages)),
                    maximum_K=float(np.max(averages)),
                    volume_average_K=float(averages[-1]),
                )
            ),
            times_s=tuple(float(value) for value in times),
            volume_average_temperature_K=tuple(float(value) for value in averages),
        )
        node_ids = np.array([3, 1, 2], dtype=np.int64)
        coordinates = np.array([[0, 0, 0], [0.5, 0, 0], [1, 0, 0]], dtype=np.float64)
        element_ids = np.array([9], dtype=np.int64)
        connectivity = np.array([[3, 1, 2]], dtype=np.int64)
        digest = mesh_sha256(node_ids, coordinates, element_ids, connectivity)
        temperatures = np.repeat(averages[:, None], 3, axis=1)
        field_path = prepared.solver_output_dir / "temperature_field.h5"
        report = write_temperature_field(
            field_path,
            TemperatureFieldData(
                node_ids=node_ids,
                coordinates_m=coordinates,
                element_ids=element_ids,
                connectivity=connectivity,
                times_s=times,
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


@pytest.mark.parametrize("case", ["lumped", "profile"])
def test_transient_cases_share_compiler_adapter_field_and_bundle(
    tmp_path: Path,
    case: str,
) -> None:
    source = tmp_path / f"{case}.step"
    source.write_bytes(case.encode())
    profile = tmp_path / "heat.csv"
    profile.write_text(
        "time_s,heat_generation_W_m3\n0,0\n5,1000\n10,500\n",
        encoding="utf-8",
    )
    if case == "lumped":
        graph: GeometryGraph = _source_backed_graph(
            synthetic_graph(GeometrySpec(kind=GeometryKind.BOX)), source
        )
        base_manifest = box_manifest()
        manifest = base_manifest.model_copy(
            update={"model": base_manifest.model.model_copy(update={"file": str(source)})}
        )
        recipe = _lumped_recipe(tmp_path / "box.manifest.yaml")
        reference = TransientReferenceKind.LUMPED_CAPACITANCE
    else:
        graph = _source_backed_graph(
            synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER)), source
        )
        manifest = _cylinder_manifest(source)
        recipe = _profile_recipe(tmp_path / "cylinder.manifest.yaml", profile.name)
        reference = TransientReferenceKind.TIME_SERIES_PROFILE
    outcome = execute_transient_run(
        run_id=f"g5-{case}",
        blueprint=_transient_blueprint(),
        manifest=manifest,
        recipe=recipe,
        graph=graph,
        mesh_policy=mesh_policy(),
        adapter=TransientReferenceFakeSolver(),
        bundle_service=RunBundleService(tmp_path / "runs"),
        reference_kind=reference,
        recipe_base_dir=tmp_path,
        allowed_input_root=tmp_path,
    )
    assert outcome.execution_status is ExecutionStatus.SUCCEEDED
    assert outcome.verification is not None
    assert outcome.verification.status is VerificationStatus.PASSED
    assert outcome.quality.result_quality is ResultQuality.PHYSICALLY_VERIFIED
    field = validate_temperature_field(outcome.bundle_path / "results/temperature_field.h5")
    assert field.valid and field.frame_count == 5
    cae_ir = ResolvedCAEIR.model_validate_json(
        (outcome.bundle_path / "resolved/cae_ir.json").read_text(encoding="utf-8")
    )
    assert cae_ir.blueprint_id == "solid_transient_thermal"
    if case == "profile":
        assert len(cae_ir.resolved_time_profiles["thermal_domain"].points) == 3
        request_hashes = json.loads(
            (outcome.bundle_path / "request/input_hashes.json").read_text(encoding="utf-8")
        )
        assert (
            request_hashes["heat_generation_profile_0.csv"]
            == hashlib.sha256(profile.read_bytes()).hexdigest()
        )


@pytest.mark.parametrize(
    ("text", "error"),
    [
        (
            "time_s,heat_generation_W_m3\n0,0\n5,100\n5,200\n10,0\n",
            "strictly increasing",
        ),
        ("time_s,heat_generation_W_m3\n0,0\n9,100\n", "end time"),
        ("time,heat\n0,0\n10,1\n", "headers"),
    ],
)
def test_profile_loader_rejects_duplicate_end_mismatch_and_wrong_headers(
    tmp_path: Path,
    text: str,
    error: str,
) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        load_heat_generation_profile(path, expected_end_time_s=10.0)


def test_transient_field_detects_requested_frame_mismatch(tmp_path: Path) -> None:
    node_ids = np.array([1], dtype=np.int64)
    coordinates = np.array([[0, 0, 0]], dtype=np.float64)
    elements = np.array([1], dtype=np.int64)
    digest = mesh_sha256(node_ids, coordinates, elements, None)
    path = tmp_path / "field.h5"
    assert write_temperature_field(
        path,
        TemperatureFieldData(
            node_ids=node_ids,
            coordinates_m=coordinates,
            element_ids=elements,
            connectivity=None,
            times_s=np.array([0, 1], dtype=np.float64),
            temperature_K=np.array([[300], [301]], dtype=np.float64),
            mesh_sha256=digest,
        ),
    ).valid
    report = validate_temperature_field(path, expected_times_s=(0.0, 2.0))
    assert not report.valid
    assert {issue.code for issue in report.issues} == {"EXPECTED_TIME_FRAME_MISMATCH"}


def test_profile_loader_enforces_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "profile.csv"
    path.write_text(
        "time_s,heat_generation_W_m3\n0,0\n10,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exceeds"):
        load_heat_generation_profile(path, expected_end_time_s=10.0, maximum_bytes=8)


def test_transient_runner_rejects_profile_path_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "cylinder.step"
    source.write_bytes(b"cylinder")
    outside = tmp_path / "outside.csv"
    outside.write_text(
        "time_s,heat_generation_W_m3\n0,0\n10,1\n",
        encoding="utf-8",
    )
    graph = _source_backed_graph(synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER)), source)
    with pytest.raises(ValueError, match="escapes"):
        execute_transient_run(
            run_id="g5-path-escape",
            blueprint=_transient_blueprint(),
            manifest=_cylinder_manifest(source),
            recipe=_profile_recipe(allowed / "manifest.yaml", str(outside)),
            graph=graph,
            mesh_policy=mesh_policy(),
            adapter=TransientReferenceFakeSolver(),
            bundle_service=RunBundleService(tmp_path / "runs"),
            reference_kind=TransientReferenceKind.TIME_SERIES_PROFILE,
            recipe_base_dir=allowed,
            allowed_input_root=allowed,
        )
