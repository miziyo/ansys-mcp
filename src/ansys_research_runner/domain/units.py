"""Unit-bearing physical quantities with explicit SI normalization."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Any

import pint
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ansys_research_runner.domain.errors import DomainError, ErrorCode

_UNITS: pint.UnitRegistry[Any] = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)


class PhysicalDimension(StrEnum):
    """Supported physical dimensions in v0.x contracts."""

    LENGTH = "length"
    AREA = "area"
    VOLUME = "volume"
    TIME = "time"
    ABSOLUTE_TEMPERATURE = "absolute_temperature"
    TEMPERATURE_DIFFERENCE = "temperature_difference"
    THERMAL_CONDUCTIVITY = "thermal_conductivity"
    DENSITY = "density"
    SPECIFIC_HEAT = "specific_heat"
    HEAT_TRANSFER_COEFFICIENT = "heat_transfer_coefficient"
    HEAT_FLUX = "heat_flux"
    VOLUMETRIC_HEAT_GENERATION = "volumetric_heat_generation"
    POWER = "power"


_SI_UNITS: dict[PhysicalDimension, str] = {
    PhysicalDimension.LENGTH: "m",
    PhysicalDimension.AREA: "m^2",
    PhysicalDimension.VOLUME: "m^3",
    PhysicalDimension.TIME: "s",
    PhysicalDimension.ABSOLUTE_TEMPERATURE: "K",
    PhysicalDimension.TEMPERATURE_DIFFERENCE: "K",
    PhysicalDimension.THERMAL_CONDUCTIVITY: "W/(m*K)",
    PhysicalDimension.DENSITY: "kg/m^3",
    PhysicalDimension.SPECIFIC_HEAT: "J/(kg*K)",
    PhysicalDimension.HEAT_TRANSFER_COEFFICIENT: "W/(m^2*K)",
    PhysicalDimension.HEAT_FLUX: "W/m^2",
    PhysicalDimension.VOLUMETRIC_HEAT_GENERATION: "W/m^3",
    PhysicalDimension.POWER: "W",
}


class PhysicalQuantity(BaseModel):
    """Original user quantity paired with a finite SI-normalized value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original: str = Field(min_length=1)
    si_value: float
    si_unit: str = Field(min_length=1)
    dimension: PhysicalDimension


def _is_delta_temperature(unit: Any) -> bool:
    text = str(unit).lower()
    return "delta" in text


def parse_quantity(
    value: Any,
    dimension: PhysicalDimension,
    *,
    path: str = "value",
) -> PhysicalQuantity:
    """Parse one explicitly unit-bearing value and normalize it to the required SI unit."""

    if isinstance(value, PhysicalQuantity):
        if value.dimension is not dimension:
            raise DomainError(
                ErrorCode.UNIT_DIMENSION_MISMATCH,
                path,
                f"Expected {dimension.value}, got {value.dimension.value}.",
            )
        return value
    if isinstance(value, dict):
        restored = PhysicalQuantity.model_validate(value)
        if restored.dimension is not dimension:
            raise DomainError(
                ErrorCode.UNIT_DIMENSION_MISMATCH,
                path,
                f"Expected {dimension.value}, got {restored.dimension.value}.",
            )
        restored_normalized = parse_quantity(restored.original, dimension, path=path)
        if restored.si_unit != restored_normalized.si_unit or not math.isclose(
            restored.si_value,
            restored_normalized.si_value,
            rel_tol=1.0e-12,
            abs_tol=1.0e-15,
        ):
            raise DomainError(
                ErrorCode.UNIT_NORMALIZATION_MISMATCH,
                path,
                "Stored SI normalization does not match the original unit-bearing value.",
            )
        return restored_normalized
    if not isinstance(value, str) or not value.strip():
        raise DomainError(
            ErrorCode.UNIT_REQUIRED,
            path,
            f"{dimension.value.replace('_', ' ').title()} requires an explicit unit.",
        )
    source = value.strip()
    try:
        parsed: pint.Quantity[Any] = _UNITS.Quantity(source)
    except (pint.PintError, TypeError, ValueError) as exc:
        raise DomainError(
            ErrorCode.UNIT_DIMENSION_MISMATCH,
            path,
            f"Could not parse unit-bearing value {source!r}.",
            details={"expected_dimension": dimension.value},
        ) from exc
    if parsed.dimensionless:
        raise DomainError(
            ErrorCode.UNIT_REQUIRED,
            path,
            f"{dimension.value.replace('_', ' ').title()} requires an explicit unit.",
        )
    is_delta = _is_delta_temperature(parsed.units)
    if dimension is PhysicalDimension.ABSOLUTE_TEMPERATURE and is_delta:
        raise DomainError(
            ErrorCode.TEMPERATURE_KIND_MISMATCH,
            path,
            "Absolute temperature cannot use a temperature-difference unit.",
        )
    if dimension is PhysicalDimension.TEMPERATURE_DIFFERENCE:
        source_lower = source.lower()
        offset_tokens = (
            "degc",
            "degf",
            "degree_celsius",
            "degree_fahrenheit",
            "°c",
            "°f",
        )
        if any(token in source_lower for token in offset_tokens) and not is_delta:
            raise DomainError(
                ErrorCode.TEMPERATURE_KIND_MISMATCH,
                path,
                "Temperature difference must use kelvin or an explicit delta temperature unit.",
            )
    si_unit = _SI_UNITS[dimension]
    try:
        normalized = parsed.to(si_unit)
    except (pint.DimensionalityError, pint.OffsetUnitCalculusError) as exc:
        raise DomainError(
            ErrorCode.UNIT_DIMENSION_MISMATCH,
            path,
            f"Expected {dimension.value}; received {parsed.units}.",
            details={"expected_unit": si_unit, "received_unit": str(parsed.units)},
        ) from exc
    magnitude = float(normalized.magnitude)
    if not math.isfinite(magnitude):
        raise DomainError(
            ErrorCode.UNIT_DIMENSION_MISMATCH,
            path,
            "Physical quantity must be finite.",
        )
    return PhysicalQuantity(
        original=source,
        si_value=magnitude,
        si_unit=si_unit,
        dimension=dimension,
    )


def quantity_from_si(value: float, dimension: PhysicalDimension) -> PhysicalQuantity:
    """Construct an internally generated quantity already expressed in SI units."""

    unit = _SI_UNITS[dimension]
    return parse_quantity(f"{value:.17g} {unit}", dimension)


def _parser(dimension: PhysicalDimension) -> BeforeValidator:
    return BeforeValidator(
        lambda value: parse_quantity(value, dimension),
        json_schema_input_type=str | PhysicalQuantity,
    )


type Length = Annotated[PhysicalQuantity, _parser(PhysicalDimension.LENGTH)]
type Area = Annotated[PhysicalQuantity, _parser(PhysicalDimension.AREA)]
type Volume = Annotated[PhysicalQuantity, _parser(PhysicalDimension.VOLUME)]
type Duration = Annotated[PhysicalQuantity, _parser(PhysicalDimension.TIME)]
type AbsoluteTemperature = Annotated[
    PhysicalQuantity, _parser(PhysicalDimension.ABSOLUTE_TEMPERATURE)
]
type TemperatureDifference = Annotated[
    PhysicalQuantity, _parser(PhysicalDimension.TEMPERATURE_DIFFERENCE)
]
type ThermalConductivity = Annotated[
    PhysicalQuantity, _parser(PhysicalDimension.THERMAL_CONDUCTIVITY)
]
type Density = Annotated[PhysicalQuantity, _parser(PhysicalDimension.DENSITY)]
type SpecificHeat = Annotated[PhysicalQuantity, _parser(PhysicalDimension.SPECIFIC_HEAT)]
type HeatTransferCoefficient = Annotated[
    PhysicalQuantity, _parser(PhysicalDimension.HEAT_TRANSFER_COEFFICIENT)
]
type HeatFlux = Annotated[PhysicalQuantity, _parser(PhysicalDimension.HEAT_FLUX)]
type VolumetricHeatGeneration = Annotated[
    PhysicalQuantity, _parser(PhysicalDimension.VOLUMETRIC_HEAT_GENERATION)
]
type Power = Annotated[PhysicalQuantity, _parser(PhysicalDimension.POWER)]
