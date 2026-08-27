"""Resolved time-profile contracts used by transient thermal workers."""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HeatGenerationPoint(BaseModel):
    """One SI-normalized heat-generation profile sample."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    time_s: float = Field(ge=0.0)
    heat_generation_W_m3: float

    @model_validator(mode="after")
    def finite(self) -> Self:
        """Reject NaN or infinity in profile samples."""

        if not math.isfinite(self.time_s) or not math.isfinite(self.heat_generation_W_m3):
            raise ValueError("Heat-generation profile values must be finite.")
        return self


class ResolvedHeatGenerationProfile(BaseModel):
    """Immutable CSV content and identity embedded in solver-bound CAE-IR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    points: tuple[HeatGenerationPoint, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def strictly_increasing(self) -> Self:
        """Require monotonic time with no duplicates."""

        times = [point.time_s for point in self.points]
        if any(right <= left for left, right in zip(times, times[1:], strict=False)):
            raise ValueError("Heat-generation profile time must be strictly increasing.")
        return self
