"""Versioned solver-bound Resolved CAE intermediate representation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.geometry import CoordinateFrame
from ansys_research_runner.domain.recipe import (
    AnalysisSettings,
    BoundaryCondition,
    MaterialAssignment,
    MeshIntent,
    RequestedOutput,
)
from ansys_research_runner.domain.selectors import SelectionEvidence
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile
from ansys_research_runner.domain.units import Length
from ansys_research_runner.domain.validation import ValidationReport


class BackendTarget(StrEnum):
    """Solver execution target selected during compilation."""

    PYMECHANICAL = "pymechanical"
    PYWORKBENCH = "pyworkbench"
    MAPDL = "mapdl"


class GeometryIdentity(BaseModel):
    """Immutable source and inspection identity for a compiled run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length_unit: str
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedMeshPolicy(BaseModel):
    """Mesh intent with the characteristic length actually selected."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str
    policy_version: int
    intent: MeshIntent
    characteristic_length: Length
    minimum_size: Length | None = None
    maximum_size: Length | None = None
    maximum_elements: int | None = None
    local_region_overrides: tuple[dict[str, Any], ...] = ()


class CompilationProvenance(BaseModel):
    """Hashes and software identity used to compile the CAE-IR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    compiled_at: str
    runner_version: str
    blueprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResolvedCAEIR(BaseModel):
    """Only input contract accepted by a solver worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    blueprint_id: str
    blueprint_version: int = Field(ge=1)
    geometry: GeometryIdentity
    coordinate_frame: CoordinateFrame
    resolved_bodies: tuple[SelectionEvidence, ...]
    resolved_faces: tuple[SelectionEvidence, ...]
    selection_evidence: dict[str, tuple[SelectionEvidence, ...]]
    materials: dict[str, MaterialAssignment]
    loads: tuple[BoundaryCondition, ...]
    boundary_conditions: tuple[BoundaryCondition, ...]
    initial_conditions: dict[str, Any]
    resolved_time_profiles: dict[str, ResolvedHeatGenerationProfile] = Field(default_factory=dict)
    analysis_settings: AnalysisSettings
    mesh_policy: ResolvedMeshPolicy
    requested_outputs: tuple[RequestedOutput, ...]
    backend_target: BackendTarget
    validation_summary: ValidationReport
    provenance: CompilationProvenance
