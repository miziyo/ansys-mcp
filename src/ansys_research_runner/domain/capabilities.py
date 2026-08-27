"""Capability-report contracts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CapabilityStatus(StrEnum):
    """Outcome of one independent capability probe."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"
    ERROR = "error"
    NOT_PROBED = "not_probed"


class PackageCapability(BaseModel):
    """Installed distribution and import capability."""

    model_config = ConfigDict(frozen=True)

    distribution: str
    module: str
    version: str | None = None
    status: CapabilityStatus
    reason: str | None = None


class ProductCapability(BaseModel):
    """Local product installation and optional live-launch result."""

    model_config = ConfigDict(frozen=True)

    product: str
    executable: Path
    installed: bool
    live_status: CapabilityStatus = CapabilityStatus.NOT_PROBED
    details: dict[str, object] = Field(default_factory=dict)
    reason: str | None = None


class HostCapability(BaseModel):
    """Host information relevant to repeatable solver runs."""

    model_config = ConfigDict(frozen=True)

    os: str
    os_release: str
    python: str
    cpu_count: int
    memory_bytes: int
    ansys_root: Path
    runtime_writable: bool


class CapabilityReport(BaseModel):
    """Complete point-in-time host capability report."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    generated_at: str
    host: HostCapability
    packages: list[PackageCapability]
    products: list[ProductCapability]
    required_mechanical_live: CapabilityStatus

    @property
    def mechanical_ready(self) -> bool:
        """Return whether the required direct Mechanical path was live-probed."""

        return self.required_mechanical_live is CapabilityStatus.AVAILABLE


class GeometryBackendCapability(BaseModel):
    """Observed capability set for one official geometry backend candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str
    status: CapabilityStatus
    package_version: str | None = None
    backend_version: str | None = None
    capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
    reason: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


class GeometryGateCapabilityReport(BaseModel):
    """Point-in-time G3 assessment against the minimum Geometry Graph contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generated_at: str
    status: Literal["PASSED", "BLOCKED_ENVIRONMENT"]
    required_capabilities: tuple[str, ...]
    selected_backend: str | None = None
    backends: tuple[GeometryBackendCapability, ...]
    blocker_reason: str | None = None

    @property
    def geometry_ready(self) -> bool:
        """Return whether one official backend satisfies the complete G3 contract."""

        return self.status == "PASSED" and self.selected_backend is not None


class SolverLaunchCapability(BaseModel):
    """Observed availability of one official solver launch mode."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str
    status: CapabilityStatus
    package_version: str | None = None
    product_version: str | None = None
    reason: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


class SteadySolverGateCapabilityReport(BaseModel):
    """G4 assessment of actual steady-thermal execution prerequisites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generated_at: str
    status: Literal["PASSED", "BLOCKED_ENVIRONMENT"]
    launch_modes: tuple[SolverLaunchCapability, ...]
    thermal_worker_live_qualified: bool
    actual_cases_succeeded: tuple[str, ...] = ()
    required_cases: tuple[str, ...] = ("steady_conduction_box", "generation_convection_cylinder")
    blocker_reason: str | None = None

    @property
    def steady_solver_ready(self) -> bool:
        """Return whether both required actual cases and the worker contract are proven."""

        return (
            self.status == "PASSED"
            and self.thermal_worker_live_qualified
            and set(self.required_cases).issubset(self.actual_cases_succeeded)
        )


class TransientSolverGateCapabilityReport(BaseModel):
    """G5 assessment of actual transient-thermal execution prerequisites."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generated_at: str
    status: Literal["PASSED", "BLOCKED_ENVIRONMENT"]
    launch_modes: tuple[SolverLaunchCapability, ...]
    thermal_worker_live_qualified: bool
    actual_cases_succeeded: tuple[str, ...] = ()
    required_cases: tuple[str, ...] = (
        "lumped_capacitance_reference",
        "time_series_heat_generation",
    )
    blocker_reason: str | None = None

    @property
    def transient_solver_ready(self) -> bool:
        """Return whether both required transient cases are proven with actual results."""

        return (
            self.status == "PASSED"
            and self.thermal_worker_live_qualified
            and set(self.required_cases).issubset(self.actual_cases_succeeded)
        )


class JobInfrastructureLiveCapabilityReport(BaseModel):
    """G6 optional actual-Mechanical lifecycle smoke assessment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    gate: Literal["G6"] = "G6"
    generated_at: str
    status: Literal["AVAILABLE", "BLOCKED_ENVIRONMENT"]
    optional_for_core_gate: Literal[True] = True
    launch_modes: tuple[SolverLaunchCapability, ...]
    mechanical_lifecycle_completed: bool
    owned_cleanup_verified: bool
    reason: str | None = None


class CapabilityArtifact(BaseModel):
    """File integrity evidence attached to a backend capability probe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    exists: bool
    size_bytes: int = Field(ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class WorkbenchCouplingGateCapabilityReport(BaseModel):
    """G7 official PyWorkbench project lifecycle and Mechanical handoff assessment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    gate: Literal["G7"] = "G7"
    generated_at: str
    status: Literal["PASSED", "BLOCKED_ENVIRONMENT", "FAILED"]
    pyworkbench_version: str | None = None
    pymechanical_version: str | None = None
    framework_version: str | None = None
    lifecycle_complete: bool
    reviewed_script_execution: bool
    project_created_saved_reopened: bool
    system_names: tuple[str, ...] = ()
    project: CapabilityArtifact | None = None
    archive: CapabilityArtifact | None = None
    mechanical_server_started: bool
    pymechanical_handoff: bool
    coupling_probe_completed: bool
    workbench_coupling: bool
    normal_exit_requested: bool
    owned_cleanup_verified: bool
    blocker_stage: str | None = None
    blocker_reason: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)
