"""Versioned thermal result and verification contracts."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ansys_research_runner.domain.geometry import Vector3


class VerificationStatus(StrEnum):
    """Allowed status values for independent result checks."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    INCONCLUSIVE = "INCONCLUSIVE"
    PARTIAL = "PARTIAL"


class ExecutionStatus(StrEnum):
    """Execution outcomes kept separate from numerical and physical quality."""

    SUCCEEDED = "SUCCEEDED"
    FAILED_LAUNCH = "FAILED_LAUNCH"
    FAILED_PRECHECK = "FAILED_PRECHECK"
    FAILED_SOLVER = "FAILED_SOLVER"
    FAILED_POSTPROCESS = "FAILED_POSTPROCESS"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    CANCELLED = "CANCELLED"


class ResultQuality(StrEnum):
    """Overall result quality without collapsing independent status dimensions."""

    NOT_AVAILABLE = "NOT_AVAILABLE"
    EXECUTION_ONLY = "EXECUTION_ONLY"
    SOLVER_CONVERGED = "SOLVER_CONVERGED"
    NUMERICALLY_VERIFIED = "NUMERICALLY_VERIFIED"
    PHYSICALLY_VERIFIED = "PHYSICALLY_VERIFIED"
    INVALID = "INVALID"


class ProbeInterpolationStatus(StrEnum):
    """Coordinate probe mapping outcome."""

    INTERPOLATED = "INTERPOLATED"
    OUTSIDE_MESH = "OUTSIDE_MESH"
    FAILED = "FAILED"


class TemperatureSummary(BaseModel):
    """Finite scalar temperature summary in kelvin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_K: float
    maximum_K: float
    volume_average_K: float | None = None

    @model_validator(mode="after")
    def validate_ordering(self) -> Self:
        """Require finite and physically ordered scalar values."""

        values = [self.minimum_K, self.maximum_K]
        if self.volume_average_K is not None:
            values.append(self.volume_average_K)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Temperature summary values must be finite.")
        if self.minimum_K > self.maximum_K:
            raise ValueError("minimum_K must not exceed maximum_K.")
        if self.volume_average_K is not None and not (
            self.minimum_K <= self.volume_average_K <= self.maximum_K
        ):
            raise ValueError("volume_average_K must lie within the reported extrema.")
        return self


class HotspotSummary(BaseModel):
    """Hottest reported location and value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position_m: Vector3
    value_K: float

    @model_validator(mode="after")
    def finite_value(self) -> Self:
        """Reject non-finite hotspot temperatures."""

        if not math.isfinite(self.value_K):
            raise ValueError("Hotspot temperature must be finite.")
        return self


class ProbeResult(BaseModel):
    """One coordinate probe with explicit mesh-mapping evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    requested_position_m: Vector3
    coordinate_system: str = Field(min_length=1)
    mapped_position_m: Vector3 | None = None
    inside_mesh: bool
    interpolation_status: ProbeInterpolationStatus
    value_K: float | None = None

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        """Prevent outside-mesh probes from carrying fabricated values."""

        if self.inside_mesh:
            if self.interpolation_status is not ProbeInterpolationStatus.INTERPOLATED:
                raise ValueError("Inside-mesh probes must have INTERPOLATED status.")
            if self.mapped_position_m is None or self.value_K is None:
                raise ValueError("Interpolated probes require a mapped point and value.")
            if not math.isfinite(self.value_K):
                raise ValueError("Probe temperature must be finite.")
        elif self.interpolation_status is ProbeInterpolationStatus.OUTSIDE_MESH:
            if self.value_K is not None:
                raise ValueError("OUTSIDE_MESH probes must not contain a temperature value.")
        return self


class ScalarResultSummary(BaseModel):
    """Versioned common scalar result payload."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    temperature: TemperatureSummary
    hotspot: HotspotSummary | None = None


class ThermalObservation(BaseModel):
    """Solver-derived values consumed by independent thermal verifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["steady"] = "steady"
    summary: ScalarResultSummary
    probes: tuple[ProbeResult, ...] = ()
    boundary_temperatures_K: dict[str, float] = Field(default_factory=dict)
    heat_input_W: float | None = None
    heat_output_W: float | None = None
    heat_generation_W: float | None = None
    heat_rejection_W: float | None = None

    @model_validator(mode="after")
    def finite_observations(self) -> Self:
        """Reject NaN and infinity in all optional scalar observations."""

        scalars = (
            *self.boundary_temperatures_K.values(),
            self.heat_input_W,
            self.heat_output_W,
            self.heat_generation_W,
            self.heat_rejection_W,
        )
        if any(value is not None and not math.isfinite(value) for value in scalars):
            raise ValueError("Thermal observations must be finite.")
        return self


class TransientThermalObservation(BaseModel):
    """Solver-derived transient volume-average temperature history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["transient"] = "transient"
    summary: ScalarResultSummary
    times_s: tuple[float, ...] = Field(min_length=1)
    volume_average_temperature_K: tuple[float, ...] = Field(min_length=1)
    probes: tuple[ProbeResult, ...] = ()

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        """Require aligned, finite, strictly increasing transient frames."""

        if len(self.times_s) != len(self.volume_average_temperature_K):
            raise ValueError("Transient time and temperature series lengths must match.")
        if not all(
            math.isfinite(value) for value in (*self.times_s, *self.volume_average_temperature_K)
        ):
            raise ValueError("Transient observation values must be finite.")
        if any(right <= left for left, right in zip(self.times_s, self.times_s[1:], strict=False)):
            raise ValueError("Transient observation times must be strictly increasing.")
        return self


class VerificationMetric(BaseModel):
    """One expected-versus-observed comparison with explicit tolerance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    expected: float
    observed: float
    unit: str = Field(min_length=1)
    absolute_error: float = Field(ge=0.0)
    relative_error: float | None = Field(default=None, ge=0.0)
    absolute_tolerance: float | None = Field(default=None, ge=0.0)
    relative_tolerance: float | None = Field(default=None, ge=0.0)
    status: VerificationStatus

    @model_validator(mode="after")
    def finite_metric(self) -> Self:
        """Ensure a metric contains finite numeric evidence."""

        numeric = (
            self.expected,
            self.observed,
            self.absolute_error,
            self.relative_error,
            self.absolute_tolerance,
            self.relative_tolerance,
        )
        if any(value is not None and not math.isfinite(value) for value in numeric):
            raise ValueError("Verification metric values must be finite.")
        if self.absolute_tolerance is None and self.relative_tolerance is None:
            raise ValueError("Verification metric requires an absolute or relative tolerance.")
        return self


class PhysicalVerificationReport(BaseModel):
    """Independent analytic or conservation verification result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    reference: str = Field(min_length=1)
    status: VerificationStatus
    metrics: tuple[VerificationMetric, ...] = ()
    checks: dict[str, VerificationStatus] = Field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    reason: str | None = None


class ExecutionQuality(BaseModel):
    """Execution state independent of correctness checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ExecutionStatus
    solver_message: str | None = None


class NumericalQuality(BaseModel):
    """Numerical convergence and discretization verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    solver_converged: bool | None = None
    mesh_verification: VerificationStatus = VerificationStatus.NOT_RUN
    time_step_verification: VerificationStatus = VerificationStatus.NOT_RUN


class PhysicalQuality(BaseModel):
    """Physical validation dimensions that must remain independently visible."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    energy_balance: VerificationStatus = VerificationStatus.NOT_RUN
    analytic_reference: VerificationStatus = VerificationStatus.NOT_RUN
    experimental_validation: VerificationStatus = VerificationStatus.NOT_AVAILABLE


class ProvenanceQuality(BaseModel):
    """Whether the run bundle has a complete provenance manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complete: bool


class ResultQualitySummary(BaseModel):
    """Versioned multidimensional result-quality statement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    execution: ExecutionQuality
    numerical: NumericalQuality
    physical: PhysicalQuality
    provenance: ProvenanceQuality
    result_quality: ResultQuality
