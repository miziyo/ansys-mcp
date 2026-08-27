"""Application service for the generic steady-thermal vertical slice."""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.adapters.solver.base import RunCallbacks, SolverAdapter
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import BackendTarget, ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import (
    MeshPolicyDocument,
    ModelManifest,
    RunRecipe,
    TemperatureFieldOutput,
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
    ThermalObservation,
    VerificationStatus,
)
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.services.compilation_service import compile_cae_ir
from ansys_research_runner.services.field_service import validate_temperature_field
from ansys_research_runner.services.run_bundle_service import (
    RunBundleService,
    model_sha256,
)
from ansys_research_runner.services.thermal_verification_service import (
    SteadyConductionReference,
    UniformGenerationConvectionReference,
    verify_steady_conduction,
    verify_uniform_generation_convection,
)


class SteadyReferenceKind(StrEnum):
    """Independent references supported by the v1 steady runner."""

    ONE_DIMENSIONAL_CONDUCTION = "one_dimensional_conduction"
    UNIFORM_GENERATION_CONVECTION = "uniform_generation_convection"


class SteadyRunOutcome(BaseModel):
    """Small application result pointing to durable run evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    execution_status: ExecutionStatus
    bundle_path: Path
    verification: PhysicalVerificationReport | None = None
    quality: ResultQualitySummary
    message: str | None = None


def _blocked_quality(status: ExecutionStatus) -> ResultQualitySummary:
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


def _verification_for(
    kind: SteadyReferenceKind,
    *,
    cae_ir: ResolvedCAEIR,
    graph: GeometryGraph,
    observation: ThermalObservation,
    conduction_reference: SteadyConductionReference | None,
    generation_reference: UniformGenerationConvectionReference | None,
) -> PhysicalVerificationReport:
    if kind is SteadyReferenceKind.ONE_DIMENSIONAL_CONDUCTION:
        return verify_steady_conduction(
            cae_ir=cae_ir,
            graph=graph,
            observation=observation,
            reference=conduction_reference,
        )
    return verify_uniform_generation_convection(
        cae_ir=cae_ir,
        graph=graph,
        observation=observation,
        reference=generation_reference,
    )


def execute_steady_run(
    *,
    run_id: str,
    blueprint: AnalysisBlueprint,
    manifest: ModelManifest,
    recipe: RunRecipe,
    graph: GeometryGraph,
    mesh_policy: MeshPolicyDocument,
    adapter: SolverAdapter,
    bundle_service: RunBundleService,
    reference_kind: SteadyReferenceKind,
    conduction_reference: SteadyConductionReference | None = None,
    generation_reference: UniformGenerationConvectionReference | None = None,
) -> SteadyRunOutcome:
    """Compile, execute, independently verify, and persist one steady thermal run."""

    paths = bundle_service.create(run_id)
    input_hashes = bundle_service.stage_request(paths, recipe=recipe, manifest=manifest)
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
    )
    bundle_service.write_resolved(
        paths,
        cae_ir=cae_ir,
        graph=graph,
        resolution=resolution,
    )
    capability = adapter.probe_capabilities()
    prepared = adapter.prepare(cae_ir, paths.work)
    precheck = adapter.precheck(prepared)
    if not precheck.valid:
        status = (
            ExecutionStatus.BLOCKED_ENVIRONMENT
            if any(issue.code == "SOLVER_CAPABILITY_MISSING" for issue in precheck.issues)
            else ExecutionStatus.FAILED_PRECHECK
        )
        quality = _blocked_quality(status)
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
        return SteadyRunOutcome(
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
        quality = _blocked_quality(solve_result.status)
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
        return SteadyRunOutcome(
            run_id=run_id,
            execution_status=solve_result.status,
            bundle_path=paths.root,
            quality=quality,
            message=solve_result.message,
        )
    postprocessed = adapter.postprocess(prepared, solve_result)
    if not isinstance(postprocessed.observation, ThermalObservation):
        raise TypeError("Steady solver returned a transient observation.")
    requires_field = any(isinstance(item, TemperatureFieldOutput) for item in recipe.outputs)
    field_report_valid = not requires_field
    if postprocessed.field_path is not None:
        destination = paths.results / "temperature_field.h5"
        if postprocessed.field_path.resolve() != destination.resolve():
            shutil.copy2(postprocessed.field_path, destination)
        field_report = validate_temperature_field(
            destination,
            expected_mesh_sha256=postprocessed.mesh_sha256,
        )
        field_report_valid = field_report.valid
    verification = _verification_for(
        reference_kind,
        cae_ir=cae_ir,
        graph=graph,
        observation=postprocessed.observation,
        conduction_reference=conduction_reference,
        generation_reference=generation_reference,
    )
    converged = solve_result.converged is True
    physically_verified = verification.status is VerificationStatus.PASSED
    execution_status = (
        ExecutionStatus.SUCCEEDED if field_report_valid else ExecutionStatus.FAILED_POSTPROCESS
    )
    energy_status = verification.checks.get("energy_balance", VerificationStatus.NOT_RUN)
    result_quality = (
        ResultQuality.PHYSICALLY_VERIFIED
        if execution_status is ExecutionStatus.SUCCEEDED and converged and physically_verified
        else ResultQuality.INVALID
    )
    quality = ResultQualitySummary(
        execution=ExecutionQuality(status=execution_status, solver_message=solve_result.message),
        numerical=NumericalQuality(solver_converged=solve_result.converged),
        physical=PhysicalQuality(
            energy_balance=energy_status,
            analytic_reference=verification.status,
        ),
        provenance=ProvenanceQuality(complete=True),
        result_quality=result_quality,
    )
    bundle_service.write_results(
        paths,
        summary=postprocessed.observation.summary,
        probes=postprocessed.observation.probes,
        verification=verification,
        quality=quality,
    )
    bundle_service.finalize(
        paths,
        started_at=solve_result.started_at or cae_ir.provenance.compiled_at,
        execution_status=execution_status,
        validation_status=verification.status,
        ansys_release=capability.product_version,
        packages=_solver_packages(capability),
        backend_capabilities=capability.model_dump(mode="json"),
        input_hashes=input_hashes,
        cae_ir_sha256=model_sha256(cae_ir),
        geometry_sha256=graph.source_sha256,
        mesh_sha256=postprocessed.mesh_sha256,
    )
    return SteadyRunOutcome(
        run_id=run_id,
        execution_status=execution_status,
        bundle_path=paths.root,
        verification=verification,
        quality=quality,
    )
