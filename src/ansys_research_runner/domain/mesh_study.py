"""Versioned mesh-refinement study contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.recipe import MeshIntent, MeshVerificationRequest
from ansys_research_runner.domain.results import ExecutionStatus, VerificationStatus


class MeshStudyExecution(StrEnum):
    """Aggregate execution status independent of convergence verification."""

    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class MeshStudyChild(BaseModel):
    """One independent child-run observation used by the aggregator."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    profile: MeshIntent
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    workdir: Path
    execution_status: ExecutionStatus
    physical_verification_status: VerificationStatus
    element_count: int | None = Field(default=None, ge=0)
    runtime_s: float = Field(ge=0.0)
    target_metric_value: float | None = None
    absolute_difference: float | None = Field(default=None, ge=0.0)
    relative_difference: float | None = Field(default=None, ge=0.0)
    message: str | None = None


class MeshStudyResult(BaseModel):
    """Aggregation that keeps execution, physics, and mesh convergence distinct."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    study_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    execution: MeshStudyExecution
    mesh_verification: VerificationStatus
    target_metric: str
    criterion: MeshVerificationRequest
    reference_profile: MeshIntent | None = None
    children: tuple[MeshStudyChild, ...]
