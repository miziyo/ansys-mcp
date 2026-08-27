"""Geometry adapter Protocol and boundary contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.selectors import RegionResolution


class TestGeometryKind(StrEnum):
    """Non-proprietary synthetic fixtures required by G2."""

    BOX = "box"
    CYLINDER = "cylinder"
    MULTI_BODY_BOX = "multi_body_box"
    AMBIGUOUS_SYMMETRIC = "ambiguous_symmetric"


class TestGeometrySpec(BaseModel):
    """Parameters for a generated synthetic geometry fixture."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TestGeometryKind
    dimensions_m: tuple[float, float, float] = (1.0, 2.0, 3.0)
    radius_m: float = Field(default=0.5, gt=0.0)
    length_m: float = Field(default=2.0, gt=0.0)


class GeometryInspectionRequest(BaseModel):
    """Request to inspect a model source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_path: Path


class GeometryCapabilityReport(BaseModel):
    """Capabilities exposed by one geometry adapter instance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str
    available: bool
    capabilities: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    package_version: str | None = None
    backend_version: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ArtifactRecord(BaseModel):
    """Small persisted geometry evidence artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Path
    media_type: str
    sha256: str


class GeometryAdapter(Protocol):
    """Common contract implemented by synthetic and official geometry adapters."""

    def probe_capabilities(self) -> GeometryCapabilityReport: ...

    def inspect(self, request: GeometryInspectionRequest) -> GeometryGraph: ...

    def generate_test_asset(self, spec: TestGeometrySpec, output_dir: Path) -> Path: ...

    def create_selection_preview(
        self,
        graph: GeometryGraph,
        resolution: RegionResolution,
        output_dir: Path,
    ) -> list[ArtifactRecord]: ...

    def close(self) -> None: ...
