"""Versioned thermal analysis Blueprint contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.geometry import EntityType


class MaterialProperty(StrEnum):
    """Material properties understood by supported thermal Blueprints."""

    THERMAL_CONDUCTIVITY = "thermal_conductivity"
    DENSITY = "density"
    SPECIFIC_HEAT = "specific_heat"


class OutputCapability(StrEnum):
    """Output types understood by supported thermal Blueprints."""

    GLOBAL_TEMPERATURE_EXTREMA = "global_temperature_extrema"
    BODY_AVERAGE_TEMPERATURE = "body_average_temperature"
    REGION_AVERAGE_TEMPERATURE = "region_average_temperature"
    COORDINATE_PROBE = "coordinate_probe"
    HOTSPOT_LOCATION = "hotspot_location"
    TEMPERATURE_FIELD = "temperature_field"


class BlueprintIdentity(BaseModel):
    """Stable Blueprint name and major contract version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)


class SupportedGeometry(BaseModel):
    """Geometry envelope accepted by a Blueprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimensions: tuple[Literal["3d"], ...] = ("3d",)
    body_types: tuple[EntityType, ...] = (EntityType.SOLID,)
    minimum_bodies: int = Field(default=1, ge=1)


class RoleCatalog(BaseModel):
    """Required and optional semantic role names."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: tuple[str, ...] = Field(min_length=1)
    optional: tuple[str, ...] = ()


class MaterialCatalog(BaseModel):
    """Required and optional material inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required: tuple[MaterialProperty, ...] = Field(min_length=1)
    optional: tuple[MaterialProperty, ...] = ()


class OutputCatalog(BaseModel):
    """Outputs supported by a Blueprint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    supported: tuple[OutputCapability, ...] = Field(min_length=1)


class AnalysisBlueprint(BaseModel):
    """CAD-independent definition of one supported thermal analysis contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    blueprint: BlueprintIdentity
    supported_geometry: SupportedGeometry
    roles: RoleCatalog
    materials: MaterialCatalog
    outputs: OutputCatalog
