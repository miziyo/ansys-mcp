"""Export versioned JSON Schemas from authoritative Pydantic contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from ansys_research_runner.domain.application import CliResponse
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.capabilities import WorkbenchCouplingGateCapabilityReport
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.jobs import (
    JobArtifactRecord,
    JobEvent,
    JobRecord,
    OwnedProcessRecord,
    ResourceSnapshot,
)
from ansys_research_runner.domain.mesh_study import MeshStudyResult
from ansys_research_runner.domain.recipe import MeshPolicyDocument, ModelManifest, RunRecipe
from ansys_research_runner.domain.results import (
    PhysicalVerificationReport,
    ResultQualitySummary,
    ScalarResultSummary,
    TransientThermalObservation,
)
from ansys_research_runner.domain.run_bundle import RunBundleManifest, RunBundleState
from ansys_research_runner.domain.selectors import RegionResolution, SelectorExpression
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile
from ansys_research_runner.io import atomic_write_text

SCHEMA_MODELS: Final[dict[str, type[BaseModel]]] = {
    "analysis-blueprint.v1.schema.json": AnalysisBlueprint,
    "cli-response.v1.schema.json": CliResponse,
    "geometry-graph.v1.schema.json": GeometryGraph,
    "job-artifact.v1.schema.json": JobArtifactRecord,
    "job-event.v1.schema.json": JobEvent,
    "job-record.v1.schema.json": JobRecord,
    "mesh-policy.v1.schema.json": MeshPolicyDocument,
    "mesh-study-result.v1.schema.json": MeshStudyResult,
    "model-manifest.v1.schema.json": ModelManifest,
    "owned-process.v1.schema.json": OwnedProcessRecord,
    "region-resolution.v1.schema.json": RegionResolution,
    "resolved-cae-ir.v1.schema.json": ResolvedCAEIR,
    "resource-snapshot.v1.schema.json": ResourceSnapshot,
    "result-quality.v1.schema.json": ResultQualitySummary,
    "run-bundle-manifest.v1.schema.json": RunBundleManifest,
    "run-bundle-state.v1.schema.json": RunBundleState,
    "run-recipe.v1.schema.json": RunRecipe,
    "scalar-result-summary.v1.schema.json": ScalarResultSummary,
    "selector-expression.v1.schema.json": SelectorExpression,
    "thermal-verification.v1.schema.json": PhysicalVerificationReport,
    "transient-observation.v1.schema.json": TransientThermalObservation,
    "transient-profile.v1.schema.json": ResolvedHeatGenerationProfile,
    "workbench-coupling-capability.v1.schema.json": WorkbenchCouplingGateCapabilityReport,
}


def rendered_schemas() -> dict[str, str]:
    """Return every versioned schema as deterministic pretty JSON."""

    rendered: dict[str, str] = {}
    for filename, model in SCHEMA_MODELS.items():
        schema = model.model_json_schema(by_alias=True, mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://ansys-research-runner.local/schemas/{filename}"
        rendered[filename] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered


def export_schemas(output_dir: Path) -> tuple[Path, ...]:
    """Write all schema files atomically and return their paths."""

    paths: list[Path] = []
    for filename, content in rendered_schemas().items():
        path = output_dir / filename
        atomic_write_text(path, content)
        paths.append(path)
    return tuple(paths)


def check_schemas(output_dir: Path) -> list[str]:
    """Return missing or stale schema filenames without modifying the tree."""

    problems: list[str] = []
    for filename, expected in rendered_schemas().items():
        path = output_dir / filename
        if not path.is_file():
            problems.append(f"missing:{filename}")
        elif path.read_text(encoding="utf-8") != expected:
            problems.append(f"stale:{filename}")
    return problems
