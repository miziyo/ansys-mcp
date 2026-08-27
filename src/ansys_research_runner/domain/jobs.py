"""Versioned job, audit, process-ownership, and artifact contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobStatus(StrEnum):
    """Closed state set for durable run orchestration."""

    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    STAGING = "STAGING"
    WAITING_RESOURCE = "WAITING_RESOURCE"
    LAUNCHING = "LAUNCHING"
    PRECHECKING = "PRECHECKING"
    SOLVING = "SOLVING"
    POSTPROCESSING = "POSTPROCESSING"
    EXPORTING = "EXPORTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_INPUT = "FAILED_INPUT"
    FAILED_LAUNCH = "FAILED_LAUNCH"
    FAILED_LICENSE = "FAILED_LICENSE"
    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_SOLVER = "FAILED_SOLVER"
    FAILED_RESOURCE = "FAILED_RESOURCE"
    FAILED_POSTPROCESS = "FAILED_POSTPROCESS"
    FAILED_EXPORT = "FAILED_EXPORT"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


TERMINAL_JOB_STATUSES = frozenset(
    {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED_INPUT,
        JobStatus.FAILED_LAUNCH,
        JobStatus.FAILED_LICENSE,
        JobStatus.FAILED_PRECHECK,
        JobStatus.FAILED_SOLVER,
        JobStatus.FAILED_RESOURCE,
        JobStatus.FAILED_POSTPROCESS,
        JobStatus.FAILED_EXPORT,
        JobStatus.CANCELLED,
    }
)

ACTIVE_JOB_STATUSES = frozenset(
    status
    for status in JobStatus
    if status not in TERMINAL_JOB_STATUSES
    and status not in {JobStatus.QUEUED, JobStatus.RECOVERY_REQUIRED}
)


class JobRecord(BaseModel):
    """Current durable snapshot for one job."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    kind: str = Field(min_length=1, max_length=128)
    request: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus
    created_at: str
    updated_at: str
    worker_id: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    attempt: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    launch_timeout_s: float = Field(default=30.0, gt=0)
    heartbeat_timeout_s: float = Field(default=30.0, gt=0)
    wall_clock_timeout_s: float = Field(default=3600.0, gt=0)


class JobEvent(BaseModel):
    """One immutable state transition or operational audit event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: int = Field(ge=1)
    job_id: str
    sequence: int = Field(ge=1)
    from_status: JobStatus | None = None
    to_status: JobStatus
    event_type: str = Field(min_length=1, max_length=128)
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class OwnedProcessRecord(BaseModel):
    """Exact process identity proven to belong to one run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str
    pid: int = Field(gt=0)
    parent_pid: int | None = Field(default=None, ge=0)
    create_time: float = Field(gt=0)
    executable_path: str
    command_line_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    launcher_pid: int = Field(gt=0)
    discovery_method: str = Field(min_length=1, max_length=128)
    registered_at: str
    ended_at: str | None = None
    termination_result: str | None = None


class JobArtifactRecord(BaseModel):
    """Immutable integrity metadata for one job artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    job_id: str
    path: str = Field(min_length=1)
    kind: str = Field(min_length=1, max_length=64)
    media_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str

    @field_validator("path")
    @classmethod
    def relative_confined_path(cls, value: str) -> str:
        """Reject absolute and parent-traversing artifact paths."""

        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or ":" in parts[0] or ".." in parts or "" in parts:
            raise ValueError("Artifact path must be a confined relative POSIX path.")
        return normalized


class ResourceSnapshot(BaseModel):
    """Best-effort resource observation for owned processes and their workdir."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    rss_bytes: int = Field(ge=0)
    cpu_time_s: float = Field(ge=0)
    workdir_bytes: int = Field(ge=0)
    observed_processes: int = Field(ge=0)
