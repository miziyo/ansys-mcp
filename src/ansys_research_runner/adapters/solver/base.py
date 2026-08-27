"""Solver adapter protocol and process-boundary result contracts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    ThermalObservation,
    TransientThermalObservation,
)
from ansys_research_runner.domain.validation import ValidationReport


class SolverCapabilityReport(BaseModel):
    """Point-in-time availability of one solver adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1)
    available: bool
    package_version: str | None = None
    product_version: str | None = None
    launch_mode: str | None = None
    capabilities: tuple[str, ...] = ()
    reason: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


class PreparedRun(BaseModel):
    """Immutable worker input staged under a run-owned working directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    workdir: Path
    cae_ir_path: Path
    source_model_path: Path
    solver_output_dir: Path

    @model_validator(mode="after")
    def require_owned_paths(self) -> Self:
        """Require staged files to remain below the prepared working directory."""

        workdir = self.workdir.resolve()
        for path in (self.cae_ir_path, self.solver_output_dir):
            if not path.resolve().is_relative_to(workdir):
                raise ValueError("Prepared solver paths must remain below the run workdir.")
        return self


class SolveResult(BaseModel):
    """Raw solver execution outcome before result extraction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    status: ExecutionStatus
    converged: bool | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    message: str | None = None
    result_files: dict[str, Path] = Field(default_factory=dict)
    evidence: dict[str, object] = Field(default_factory=dict)


class PostprocessResult(BaseModel):
    """Solver-neutral postprocessed result returned to application services."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation: ThermalObservation | TransientThermalObservation = Field(
        discriminator="analysis_type"
    )
    field_path: Path | None = None
    mesh_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    artifacts: dict[str, Path] = Field(default_factory=dict)


class RunCallbacks(BaseModel):
    """Callbacks excluded from serialization and supplied only inside a worker."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    heartbeat: Callable[[], None]
    log: Callable[[str], None]


class SolverAdapter(Protocol):
    """Backend contract accepted by the thermal application service."""

    def probe_capabilities(self) -> SolverCapabilityReport:
        """Return current backend availability without inventing support."""

    def prepare(self, cae_ir: ResolvedCAEIR, workdir: Path) -> PreparedRun:
        """Stage immutable solver input in a run-owned directory."""

    def precheck(self, prepared: PreparedRun) -> ValidationReport:
        """Validate staged input before a solver process is launched."""

    def solve(self, prepared: PreparedRun, callbacks: RunCallbacks) -> SolveResult:
        """Execute the isolated solver worker."""

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        """Extract solver-neutral values and artifacts."""

    def request_cancel(self, prepared: PreparedRun) -> None:
        """Request cancellation for the exact run-owned process set."""

    def close(self) -> None:
        """Release adapter-owned resources."""
