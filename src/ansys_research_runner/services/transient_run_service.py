"""Application service for generic transient-thermal vertical slices."""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.adapters.solver.base import RunCallbacks, SolverAdapter
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import BackendTarget
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import (
    MeshPolicyDocument,
    ModelManifest,
    RunRecipe,
    TemperatureFieldOutput,
    TimeSeriesVolumetricHeatLoad,
)
from ansys_research_runner.domain.results import (
    ExecutionQuality,
    ExecutionStatus,
    NumericalQuality,
    PhysicalQuality,
    PhysicalVerificationReport,
    ProvenanceQuality,
    ResultQuality,
    ResultQualitySummary,
    TransientThermalObservation,
    VerificationStatus,
)
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile
from ansys_research_runner.services.compilation_service import compile_cae_ir
from ansys_research_runner.services.field_service import validate_temperature_field
from ansys_research_runner.services.run_bundle_service import RunBundleService, model_sha256
from ansys_research_runner.services.transient_profile_service import load_heat_generation_profile
from ansys_research_runner.services.transient_verification_service import (
    LumpedCapacitanceReference,
    verify_lumped_capacitance,
    verify_profile_time_alignment,
)


class TransientReferenceKind(StrEnum):
    """Independent transient checks supported in v1."""

    LUMPED_CAPACITANCE = "lumped_capacitance"
    TIME_SERIES_PROFILE = "time_series_profile"


class TransientRunOutcome(BaseModel):
    """Small application result pointing to durable transient evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    execution_status: ExecutionStatus
    bundle_path: Path
    verification: PhysicalVerificationReport | None = None
    quality: ResultQualitySummary
    message: str | None = None


def _unavailable_quality(status: ExecutionStatus) -> ResultQualitySummary:
    return ResultQualitySummary(
        execution=ExecutionQuality(status=status),
        numerical=NumericalQuality(),
        physical=PhysicalQuality(),
        provenance=ProvenanceQuality(complete=True),
        result_quality=ResultQuality.NOT_AVAILABLE,
    )


def _solver_packages(capability: object) -> dict[str, str]:
    package_version = getattr(capability, "package_version", None)
    backend = str(getattr(capability, "backend", "solver"))
    if backend == "prime_mapdl_dpf":
        evidence = getattr(capability, "evidence", {})
        return {
            "ansys-meshing-prime": str(package_version or "unknown"),
            "ansys-dpf-core": str(evidence.get("ansys_dpf_core") or "unknown"),
        }
    return {"ansys-mechanical-core": str(package_version or "unknown")}


def _resolve_profile_path(profile_file: str, base: Path, allowed_root: Path) -> Path:
    source = Path(profile_file).expanduser()
    resolved = (source if source.is_absolute() else base / source).resolve()
    if not resolved.is_relative_to(allowed_root.resolve()):
        raise ValueError("Heat-generation profile escapes the allowed input root.")
    return resolved


def _profiles(
    *,
    recipe: RunRecipe,
    recipe_base_dir: Path,
    allowed_input_root: Path,
) -> tuple[dict[str, ResolvedHeatGenerationProfile], dict[str, Path]]:
    if recipe.analysis.end_time is None:
        raise ValueError("Transient recipe requires analysis.end_time.")
    resolved: dict[str, ResolvedHeatGenerationProfile] = {}
    staged: dict[str, Path] = {}
    profile_loads = [
        condition
        for condition in recipe.boundary_conditions
        if isinstance(condition, TimeSeriesVolumetricHeatLoad)
    ]
    for index, load in enumerate(profile_loads):
        if load.region in resolved:
            raise ValueError(f"Multiple time profiles target region {load.region!r}.")
        source = _resolve_profile_path(
            load.profile_file,
            recipe_base_dir,
            allowed_input_root,
        )
        resolved[load.region] = load_heat_generation_profile(
            source,
            expected_end_time_s=recipe.analysis.end_time.si_value,
        )
        staged[f"heat_generation_profile_{index}.csv"] = source
    return resolved, staged


def execute_transient_run(
    *,
    run_id: str,
    blueprint: AnalysisBlueprint,
    manifest: ModelManifest,
    recipe: RunRecipe,
    graph: GeometryGraph,
    mesh_policy: MeshPolicyDocument,
    adapter: SolverAdapter,
    bundle_service: RunBundleService,
    reference_kind: TransientReferenceKind,
    recipe_base_dir: Path,
    allowed_input_root: Path,
    lumped_reference: LumpedCapacitanceReference | None = None,
) -> TransientRunOutcome:
    """Compile, execute, verify, and persist one transient thermal run."""

    if recipe.analysis.type != "transient":
        raise ValueError("Transient runner requires analysis.type=transient.")
    resolved_profiles, staged_profiles = _profiles(
        recipe=recipe,
        recipe_base_dir=recipe_base_dir.resolve(),
        allowed_input_root=allowed_input_root.resolve(),
    )
    paths = bundle_service.create(run_id)
    input_hashes = bundle_service.stage_request(
        paths,
        recipe=recipe,
        manifest=manifest,
        auxiliary_files=staged_profiles,
    )
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id=run_id,
        blueprint=blueprint,
        manifest=manifest,
        recipe=recipe,
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy,
        backend_target=BackendTarget.MAPDL,
        resolved_time_profiles=resolved_profiles,
    )
    bundle_service.write_resolved(paths, cae_ir=cae_ir, graph=graph, resolution=resolution)
    capability = adapter.probe_capabilities()
    prepared = adapter.prepare(cae_ir, paths.work)
    precheck = adapter.precheck(prepared)
    if not precheck.valid:
        status = (
            ExecutionStatus.BLOCKED_ENVIRONMENT
            if any(issue.code == "SOLVER_CAPABILITY_MISSING" for issue in precheck.issues)
            else ExecutionStatus.FAILED_PRECHECK
        )
        quality = _unavailable_quality(status)
        bundle_service.finalize(
            paths,
            started_at=cae_ir.provenance.compiled_at,
            execution_status=status,
            validation_status=VerificationStatus.NOT_RUN,
            ansys_release=capability.product_version,
            packages=_solver_packages(capability),
            backend_capabilities=capability.model_dump(mode="json"),
            input_hashes=input_hashes,
            cae_ir_sha256=model_sha256(cae_ir),
            geometry_sha256=graph.source_sha256,
            mesh_sha256=None,
        )
        return TransientRunOutcome(
            run_id=run_id,
            execution_status=status,
            bundle_path=paths.root,
            quality=quality,
            message="; ".join(issue.message for issue in precheck.issues),
        )
    solve_result = adapter.solve(
        prepared,
        RunCallbacks(heartbeat=lambda: None, log=lambda _: None),
    )
    if solve_result.status is not ExecutionStatus.SUCCEEDED:
        quality = _unavailable_quality(solve_result.status)
        bundle_service.finalize(
            paths,
            started_at=solve_result.started_at or cae_ir.provenance.compiled_at,
            execution_status=solve_result.status,
            validation_status=VerificationStatus.NOT_RUN,
            ansys_release=capability.product_version,
            packages=_solver_packages(capability),
            backend_capabilities=capability.model_dump(mode="json"),
            input_hashes=input_hashes,
            cae_ir_sha256=model_sha256(cae_ir),
            geometry_sha256=graph.source_sha256,
            mesh_sha256=None,
        )
        return TransientRunOutcome(
            run_id=run_id,
            execution_status=solve_result.status,
            bundle_path=paths.root,
            quality=quality,
            message=solve_result.message,
        )
    postprocessed = adapter.postprocess(prepared, solve_result)
    if not isinstance(postprocessed.observation, TransientThermalObservation):
        raise TypeError("Transient solver returned a steady observation.")
    observation = postprocessed.observation
    requires_field = any(isinstance(item, TemperatureFieldOutput) for item in recipe.outputs)
    field_valid = not requires_field
    if postprocessed.field_path is not None:
        destination = paths.results / "temperature_field.h5"
        if postprocessed.field_path.resolve() != destination.resolve():
            shutil.copy2(postprocessed.field_path, destination)
        field_valid = validate_temperature_field(
            destination,
            expected_mesh_sha256=postprocessed.mesh_sha256,
            expected_times_s=observation.times_s,
        ).valid
    assert recipe.analysis.end_time is not None
    if reference_kind is TransientReferenceKind.LUMPED_CAPACITANCE:
        verification = verify_lumped_capacitance(
            cae_ir=cae_ir,
            graph=graph,
            observation=observation,
            reference=lumped_reference,
        )
    else:
        if len(resolved_profiles) != 1:
            raise ValueError("Time-series reference requires exactly one resolved profile.")
        verification = verify_profile_time_alignment(
            profile=next(iter(resolved_profiles.values())),
            observation=observation,
            expected_end_time_s=recipe.analysis.end_time.si_value,
        )
    converged = solve_result.converged is True
    verified = verification.status is VerificationStatus.PASSED
    status = ExecutionStatus.SUCCEEDED if field_valid else ExecutionStatus.FAILED_POSTPROCESS
    quality = ResultQualitySummary(
        execution=ExecutionQuality(status=status, solver_message=solve_result.message),
        numerical=NumericalQuality(
            solver_converged=solve_result.converged,
            time_step_verification=(
                VerificationStatus.PASSED if field_valid else VerificationStatus.FAILED
            ),
        ),
        physical=PhysicalQuality(analytic_reference=verification.status),
        provenance=ProvenanceQuality(complete=True),
        result_quality=(
            ResultQuality.PHYSICALLY_VERIFIED
            if status is ExecutionStatus.SUCCEEDED and converged and verified
            else ResultQuality.INVALID
        ),
    )
    bundle_service.write_results(
        paths,
        summary=observation.summary,
        probes=observation.probes,
        verification=verification,
        quality=quality,
    )
    bundle_service.finalize(
        paths,
        started_at=solve_result.started_at or cae_ir.provenance.compiled_at,
        execution_status=status,
        validation_status=verification.status,
        ansys_release=capability.product_version,
        packages=_solver_packages(capability),
        backend_capabilities=capability.model_dump(mode="json"),
        input_hashes=input_hashes,
        cae_ir_sha256=model_sha256(cae_ir),
        geometry_sha256=graph.source_sha256,
        mesh_sha256=postprocessed.mesh_sha256,
    )
    return TransientRunOutcome(
        run_id=run_id,
        execution_status=status,
        bundle_path=paths.root,
        verification=verification,
        quality=quality,
    )
