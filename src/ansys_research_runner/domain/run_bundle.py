"""Versioned provenance and state contracts for one immutable run bundle."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus


class ArtifactKind(StrEnum):
    """Run-bundle artifact categories."""

    REQUEST = "request"
    RESOLVED = "resolved"
    RESULT = "result"
    ARTIFACT = "artifact"
    LOG = "log"


class RunBundlePhase(StrEnum):
    """Monotonic bundle-production phases before the job registry exists."""

    CREATED = "CREATED"
    REQUEST_STAGED = "REQUEST_STAGED"
    RESOLVED = "RESOLVED"
    SOLVED = "SOLVED"
    POSTPROCESSED = "POSTPROCESSED"
    FINALIZED = "FINALIZED"


class ArtifactDigest(BaseModel):
    """Relative artifact identity and integrity evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    kind: ArtifactKind
    media_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunBundleManifest(BaseModel):
    """Complete versioned provenance manifest for one run bundle."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    started_at: str
    finished_at: str | None = None
    host_os: str
    python_version: str
    ansys_release: str | None = None
    pyansys_packages: dict[str, str] = Field(default_factory=dict)
    backend_capabilities: dict[str, Any] = Field(default_factory=dict)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    cae_ir_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    geometry_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mesh_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_status: ExecutionStatus
    validation_status: VerificationStatus
    artifacts: tuple[ArtifactDigest, ...] = ()
    git_commit: str | None = None
    git_dirty: bool | None = None


class RunBundleState(BaseModel):
    """Small atomic state snapshot stored beside the bundle manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    phase: RunBundlePhase
    execution_status: ExecutionStatus | None = None
    updated_at: str
    detail: str | None = None
