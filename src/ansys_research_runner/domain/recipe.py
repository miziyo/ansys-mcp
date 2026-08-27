"""Versioned Model Manifest and Run Recipe contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ansys_research_runner.domain.geometry import CoordinateFrame, Vector3
from ansys_research_runner.domain.selectors import RoleDefinition
from ansys_research_runner.domain.units import (
    AbsoluteTemperature,
    Density,
    Duration,
    HeatFlux,
    HeatTransferCoefficient,
    Length,
    PhysicalDimension,
    Power,
    SpecificHeat,
    ThermalConductivity,
    VolumetricHeatGeneration,
    parse_quantity,
)


class BlueprintReference(BaseModel):
    """Reference to a versioned Blueprint, accepting ``name@version`` input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def parse_reference(cls, value: Any) -> Any:
        """Parse the compact public reference form."""

        if not isinstance(value, str):
            return value
        identifier, separator, version = value.rpartition("@")
        if not separator or not version.isdigit():
            raise ValueError("Blueprint reference must use name@positive-integer format.")
        return {"id": identifier, "version": int(version)}

    def compact(self) -> str:
        """Return the compact public reference form."""

        return f"{self.id}@{self.version}"


class ModelSource(BaseModel):
    """Source CAD reference and its explicit length unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str = Field(min_length=1)
    length_unit: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_length_unit(self) -> Self:
        """Ensure the declared model unit is a real length unit."""

        parse_quantity(f"1 {self.length_unit}", PhysicalDimension.LENGTH, path="model.length_unit")
        return self


class ModelManifest(BaseModel):
    """Thin data-only mapping from one CAD model to semantic roles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    model: ModelSource
    coordinate_frame: CoordinateFrame = CoordinateFrame()
    roles: dict[str, RoleDefinition] = Field(min_length=1)


class RunIdentity(BaseModel):
    """Identity and immutable input references for one requested run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    blueprint: BlueprintReference
    model_manifest: str = Field(min_length=1)


class MaterialAssignment(BaseModel):
    """Thermal material properties assigned to one semantic body role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thermal_conductivity: ThermalConductivity
    density: Density | None = None
    specific_heat: SpecificHeat | None = None


class TemperatureBoundary(BaseModel):
    """Prescribed absolute temperature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["temperature"]
    region: str = Field(min_length=1)
    value: AbsoluteTemperature


class ConvectionBoundary(BaseModel):
    """Convection film condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["convection"]
    region: str = Field(min_length=1)
    film_coefficient: HeatTransferCoefficient
    ambient_temperature: AbsoluteTemperature


class HeatFluxBoundary(BaseModel):
    """Surface heat-flux condition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["heat_flux"]
    region: str = Field(min_length=1)
    value: HeatFlux


class TotalHeatBoundary(BaseModel):
    """Total heat applied to a semantic region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["total_heat"]
    region: str = Field(min_length=1)
    value: Power


class VolumetricHeatLoad(BaseModel):
    """Uniform volumetric heat generation in a body role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["volumetric_heat_generation"]
    region: str = Field(min_length=1)
    value: VolumetricHeatGeneration


class TimeSeriesVolumetricHeatLoad(BaseModel):
    """Volumetric heat generation read from a bounded two-column CSV profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["volumetric_heat_generation_profile"]
    region: str = Field(min_length=1)
    profile_file: str = Field(min_length=1)


type BoundaryCondition = Annotated[
    TemperatureBoundary
    | ConvectionBoundary
    | HeatFluxBoundary
    | TotalHeatBoundary
    | VolumetricHeatLoad
    | TimeSeriesVolumetricHeatLoad,
    Field(discriminator="type"),
]


class MeshIntent(StrEnum):
    """Supported mesh-density intents."""

    COARSE = "coarse"
    BALANCED = "balanced"
    FINE = "fine"


class LocalMeshOverride(BaseModel):
    """Optional local characteristic length for a semantic region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    region: str = Field(min_length=1)
    size: Length


class MeshRequest(BaseModel):
    """Bounded v0.x mesh intent and optional resource constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: MeshIntent = MeshIntent.BALANCED
    minimum_size: Length | None = None
    maximum_size: Length | None = None
    maximum_elements: int | None = Field(default=None, ge=1)
    local_region_overrides: tuple[LocalMeshOverride, ...] = ()

    @model_validator(mode="after")
    def validate_sizes(self) -> Self:
        """Require minimum size to be no greater than maximum size."""

        if (
            self.minimum_size is not None
            and self.maximum_size is not None
            and self.minimum_size.si_value > self.maximum_size.si_value
        ):
            raise ValueError("minimum_size must not exceed maximum_size.")
        return self


class MeshVerificationRequest(BaseModel):
    """Practical convergence criterion for an explicit three-profile mesh study."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: tuple[MeshIntent, ...] = (
        MeshIntent.COARSE,
        MeshIntent.BALANCED,
        MeshIntent.FINE,
    )
    target_metric: Literal[
        "minimum_temperature_K",
        "maximum_temperature_K",
        "volume_average_temperature_K",
    ] = "volume_average_temperature_K"
    relative_tolerance: float | None = Field(default=None, ge=0.0)
    absolute_tolerance_K: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def require_complete_practical_study(self) -> Self:
        """Require all named profiles and at least one explicit tolerance."""

        if len(self.profiles) != 3 or set(self.profiles) != set(MeshIntent):
            raise ValueError("mesh verification profiles must be coarse, balanced, and fine.")
        if self.relative_tolerance is None and self.absolute_tolerance_K is None:
            raise ValueError("mesh verification requires a relative or absolute tolerance.")
        return self


class MeshIntentPolicy(BaseModel):
    """Versioned characteristic-length divisor for one mesh intent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    diagonal_divisor: float = Field(gt=0.0)


class MeshPolicyDocument(BaseModel):
    """Versioned table of supported mesh-intent policies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    policy_id: Literal["thermal_mesh_intent"] = "thermal_mesh_intent"
    policies: dict[MeshIntent, MeshIntentPolicy]

    @model_validator(mode="after")
    def require_all_intents(self) -> Self:
        """Require an explicit policy for every supported intent."""

        if set(self.policies) != set(MeshIntent):
            raise ValueError("Mesh policy document must define coarse, balanced, and fine.")
        return self


class GlobalTemperatureExtremaOutput(BaseModel):
    """Request global minimum and maximum temperature."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["global_temperature_extrema"]


class BodyAverageTemperatureOutput(BaseModel):
    """Request average temperature for a body role."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["body_average_temperature"]
    region: str = Field(min_length=1)


class RegionAverageTemperatureOutput(BaseModel):
    """Request average temperature for any semantic region."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["region_average_temperature"]
    region: str = Field(min_length=1)


class PointUnit(StrEnum):
    """Supported coordinate-probe point conventions."""

    MODEL_LENGTH_UNIT = "model_length_unit"
    NORMALIZED_MODEL_COORDINATES = "normalized_model_coordinates"


class CoordinateProbeOutput(BaseModel):
    """Request interpolated temperature at a coordinate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["coordinate_probe"]
    name: str = Field(min_length=1)
    point: Vector3
    point_unit: PointUnit


class HotspotLocationOutput(BaseModel):
    """Request the hottest reported location."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["hotspot_location"]


class TemperatureFieldOutput(BaseModel):
    """Request a persisted temperature field artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["temperature_field"]
    times: tuple[Duration, ...] = ()


type RequestedOutput = Annotated[
    GlobalTemperatureExtremaOutput
    | BodyAverageTemperatureOutput
    | RegionAverageTemperatureOutput
    | CoordinateProbeOutput
    | HotspotLocationOutput
    | TemperatureFieldOutput,
    Field(discriminator="type"),
]


class AnalysisSettings(BaseModel):
    """Steady or transient time controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["steady", "transient"] = "steady"
    initial_temperature: AbsoluteTemperature | None = None
    end_time: Duration | None = None
    time_step: Duration | None = None

    @model_validator(mode="after")
    def validate_transient_controls(self) -> Self:
        """Require positive time controls only for transient analysis."""

        if self.type == "steady" and (
            self.initial_temperature is not None
            or self.end_time is not None
            or self.time_step is not None
        ):
            raise ValueError("steady analysis cannot define transient initial or time controls.")
        if self.type == "transient":
            if self.initial_temperature is None or self.end_time is None or self.time_step is None:
                raise ValueError(
                    "transient analysis requires initial_temperature, end_time, and time_step."
                )
            if self.end_time.si_value <= 0.0 or self.time_step.si_value <= 0.0:
                raise ValueError("transient time controls must be positive.")
            if self.time_step.si_value > self.end_time.si_value:
                raise ValueError("time_step must not exceed end_time.")
        return self


class RunRecipe(BaseModel):
    """Per-run values compiled with a Blueprint and Model Manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    run: RunIdentity
    materials: dict[str, MaterialAssignment] = Field(min_length=1)
    boundary_conditions: tuple[BoundaryCondition, ...]
    analysis: AnalysisSettings = AnalysisSettings()
    mesh: MeshRequest = MeshRequest()
    mesh_verification: MeshVerificationRequest | None = None
    outputs: tuple[RequestedOutput, ...] = Field(min_length=1)
