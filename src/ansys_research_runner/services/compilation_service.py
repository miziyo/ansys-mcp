"""Compile validated public contracts into a solver-bound CAE-IR."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ansys_research_runner import __version__
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import (
    BackendTarget,
    CompilationProvenance,
    GeometryIdentity,
    ResolvedCAEIR,
    ResolvedMeshPolicy,
)
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import (
    ConvectionBoundary,
    MeshPolicyDocument,
    ModelManifest,
    RunRecipe,
    TemperatureBoundary,
)
from ansys_research_runner.domain.selectors import EntityKind, RegionResolution
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile
from ansys_research_runner.domain.units import PhysicalDimension, quantity_from_si
from ansys_research_runner.domain.validation import validate_run_contracts


def _sha256_model(model: Any) -> str:
    payload = model.model_dump(mode="json", by_alias=True)
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def compile_cae_ir(
    *,
    run_id: str,
    blueprint: AnalysisBlueprint,
    manifest: ModelManifest,
    recipe: RunRecipe,
    graph: GeometryGraph,
    resolution: RegionResolution,
    mesh_policy: MeshPolicyDocument,
    backend_target: BackendTarget = BackendTarget.PYMECHANICAL,
    compiled_at: str | None = None,
    resolved_time_profiles: dict[str, ResolvedHeatGenerationProfile] | None = None,
) -> ResolvedCAEIR:
    """Compile validated domain inputs without reinterpreting user YAML in the worker."""

    validation = validate_run_contracts(
        blueprint,
        manifest.roles,
        recipe,
        graph,
        resolution,
    )
    if not validation.valid:
        raise DomainError(
            ErrorCode.PREFLIGHT_VALIDATION_FAILED,
            "run",
            "Run contracts failed preflight validation.",
            details={"issues": [item.model_dump(mode="json") for item in validation.issues]},
        )
    all_evidence = [evidence for role in resolution.roles.values() for evidence in role.evidence]
    bodies = tuple(item for item in all_evidence if item.entity is EntityKind.BODY)
    faces = tuple(item for item in all_evidence if item.entity is EntityKind.FACE)
    policy = mesh_policy.policies[recipe.mesh.intent]
    minimum = [body.bounding_box.minimum.root for body in graph.bodies]
    maximum = [body.bounding_box.maximum.root for body in graph.bodies]
    extents = [
        max(point[index] for point in maximum) - min(point[index] for point in minimum)
        for index in range(3)
    ]
    diagonal = sum(value * value for value in extents) ** 0.5
    characteristic = quantity_from_si(
        diagonal / policy.diagonal_divisor,
        PhysicalDimension.LENGTH,
    )
    local_overrides = tuple(
        {
            "region": override.region,
            "size": override.size.model_dump(mode="json"),
        }
        for override in recipe.mesh.local_region_overrides
    )
    loads = tuple(
        condition
        for condition in recipe.boundary_conditions
        if not isinstance(condition, (TemperatureBoundary, ConvectionBoundary))
    )
    boundaries = tuple(
        condition
        for condition in recipe.boundary_conditions
        if isinstance(condition, (TemperatureBoundary, ConvectionBoundary))
    )
    selection_evidence = {
        role: result.evidence for role, result in sorted(resolution.roles.items())
    }
    initial_conditions: dict[str, Any] = {}
    if recipe.analysis.initial_temperature is not None:
        initial_conditions["temperature"] = recipe.analysis.initial_temperature.model_dump(
            mode="json"
        )
    return ResolvedCAEIR(
        run_id=run_id,
        blueprint_id=blueprint.blueprint.id,
        blueprint_version=blueprint.blueprint.version,
        geometry=GeometryIdentity(
            # Inspection already resolved and confined the public manifest path.
            # Persist that immutable source path so an external worker does not
            # reinterpret a relative path against a different working directory.
            file=graph.source_path,
            sha256=graph.source_sha256,
            length_unit=manifest.model.length_unit,
            fingerprint=graph.fingerprint(),
        ),
        coordinate_frame=manifest.coordinate_frame,
        resolved_bodies=bodies,
        resolved_faces=faces,
        selection_evidence=selection_evidence,
        materials=recipe.materials,
        loads=loads,
        boundary_conditions=boundaries,
        initial_conditions=initial_conditions,
        resolved_time_profiles=resolved_time_profiles or {},
        analysis_settings=recipe.analysis,
        mesh_policy=ResolvedMeshPolicy(
            policy_id=mesh_policy.policy_id,
            policy_version=mesh_policy.schema_version,
            intent=recipe.mesh.intent,
            characteristic_length=characteristic,
            minimum_size=recipe.mesh.minimum_size,
            maximum_size=recipe.mesh.maximum_size,
            maximum_elements=recipe.mesh.maximum_elements,
            local_region_overrides=local_overrides,
        ),
        requested_outputs=recipe.outputs,
        backend_target=backend_target,
        validation_summary=validation,
        provenance=CompilationProvenance(
            compiled_at=compiled_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            runner_version=__version__,
            blueprint_sha256=_sha256_model(blueprint),
            manifest_sha256=_sha256_model(manifest),
            recipe_sha256=_sha256_model(recipe),
        ),
    )
