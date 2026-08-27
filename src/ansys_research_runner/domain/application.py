"""Versioned command results and the stable CLI JSON envelope."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.jobs import JobArtifactRecord, JobEvent, JobRecord, JobStatus
from ansys_research_runner.domain.selectors import RegionResolution
from ansys_research_runner.domain.validation import ValidationReport


class CliCommand(StrEnum):
    """Commands with versioned machine-readable output."""

    DOCTOR = "doctor"
    GEOMETRY_DOCTOR = "geometry-doctor"
    SOLVER_DOCTOR = "solver-doctor"
    INSPECT = "inspect"
    RESOLVE = "resolve"
    VALIDATE = "validate"
    PLAN = "plan"
    RUN = "run"
    STATUS = "status"
    CANCEL = "cancel"
    RESULTS = "results"
    ARTIFACTS = "artifacts"
    RECOVER = "recover"


class CommandError(BaseModel):
    """Stable machine-readable failure payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class DoctorCommandResult(BaseModel):
    """Capability evidence returned by a diagnostic command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    report: dict[str, Any]


class InspectCommandResult(BaseModel):
    """Geometry inspection result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    model_path: str
    backend: str
    geometry: GeometryGraph


class ResolveCommandResult(BaseModel):
    """Semantic-region resolution result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    recipe_path: str
    manifest_path: str
    model_path: str
    resolution: RegionResolution


class ValidateCommandResult(BaseModel):
    """Cross-contract preflight validation result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    recipe_path: str
    validation: ValidationReport


class PlanCommandResult(BaseModel):
    """Solver-bound, reviewed analysis plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    recipe_path: str
    run_id: str
    cae_ir: ResolvedCAEIR


class RunCommandResult(BaseModel):
    """Durable non-blocking job submission result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job: JobRecord


class StatusCommandResult(BaseModel):
    """Current job snapshot plus immutable audit history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job: JobRecord
    events: tuple[JobEvent, ...]


class ResultsCommandResult(BaseModel):
    """Small result summary; field arrays remain in artifacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str
    status: JobStatus
    worker_result: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None


class ArtifactsCommandResult(BaseModel):
    """Integrity metadata for artifacts owned by a job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str
    artifacts: tuple[JobArtifactRecord, ...]


class RecoveryCommandResult(BaseModel):
    """Safe automatic recovery outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    recovered: tuple[str, ...]
    requeued: tuple[str, ...]
    manual_required: tuple[str, ...]


type CommandData = (
    DoctorCommandResult
    | InspectCommandResult
    | ResolveCommandResult
    | ValidateCommandResult
    | PlanCommandResult
    | RunCommandResult
    | StatusCommandResult
    | ResultsCommandResult
    | ArtifactsCommandResult
    | RecoveryCommandResult
)


class CliResponse(BaseModel):
    """Stable success/failure envelope emitted by every G8 command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    command: CliCommand
    ok: bool
    data: CommandData | None = None
    error: CommandError | None = None

    @model_validator(mode="after")
    def exactly_one_payload(self) -> Self:
        """Require data on success and an error on failure."""

        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("Successful CLI responses require data and forbid error.")
        if not self.ok and (self.error is None or self.data is not None):
            raise ValueError("Failed CLI responses require error and forbid data.")
        return self

    @classmethod
    def success(cls, command: CliCommand, data: CommandData) -> CliResponse:
        """Build a successful response."""

        return cls(command=command, ok=True, data=data)

    @classmethod
    def failure(cls, command: CliCommand, error: CommandError) -> CliResponse:
        """Build a failed response."""

        return cls(command=command, ok=False, error=error)
