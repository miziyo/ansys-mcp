"""G2 physical quantity tests."""

from __future__ import annotations

import math

import pytest

from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.units import (
    PhysicalDimension,
    parse_quantity,
    quantity_from_si,
)


@pytest.mark.parametrize(
    ("source", "dimension", "expected"),
    [
        ("2 mm", PhysicalDimension.LENGTH, 0.002),
        ("4 cm^2", PhysicalDimension.AREA, 0.0004),
        ("3 liter", PhysicalDimension.VOLUME, 0.003),
        ("1 hour", PhysicalDimension.TIME, 3600.0),
        ("15 W/m/K", PhysicalDimension.THERMAL_CONDUCTIVITY, 15.0),
        ("7800 kg/m^3", PhysicalDimension.DENSITY, 7800.0),
        ("500 J/kg/K", PhysicalDimension.SPECIFIC_HEAT, 500.0),
        ("8 W/m^2/K", PhysicalDimension.HEAT_TRANSFER_COEFFICIENT, 8.0),
        ("12 W/m^2", PhysicalDimension.HEAT_FLUX, 12.0),
        ("9 W/m^3", PhysicalDimension.VOLUMETRIC_HEAT_GENERATION, 9.0),
        ("25 W", PhysicalDimension.POWER, 25.0),
    ],
)
def test_quantity_normalizes_to_si(
    source: str, dimension: PhysicalDimension, expected: float
) -> None:
    quantity = parse_quantity(source, dimension)
    assert quantity.original == source
    assert quantity.si_value == pytest.approx(expected)
    assert quantity.dimension is dimension


def test_absolute_temperature_preserves_offset_semantics() -> None:
    quantity = parse_quantity("100 degC", PhysicalDimension.ABSOLUTE_TEMPERATURE)
    assert quantity.si_value == pytest.approx(373.15)
    assert quantity.si_unit == "K"


def test_temperature_difference_requires_delta_or_kelvin() -> None:
    with pytest.raises(DomainError) as failure:
        parse_quantity("20 degC", PhysicalDimension.TEMPERATURE_DIFFERENCE)
    assert failure.value.code is ErrorCode.TEMPERATURE_KIND_MISMATCH
    assert parse_quantity(
        "20 delta_degC", PhysicalDimension.TEMPERATURE_DIFFERENCE
    ).si_value == pytest.approx(20.0)
    assert parse_quantity(
        "18 delta_degF", PhysicalDimension.TEMPERATURE_DIFFERENCE
    ).si_value == pytest.approx(10.0)
    with pytest.raises(DomainError):
        parse_quantity("50 degF", PhysicalDimension.TEMPERATURE_DIFFERENCE)


def test_absolute_temperature_rejects_delta_unit() -> None:
    with pytest.raises(DomainError) as failure:
        parse_quantity("5 delta_degC", PhysicalDimension.ABSOLUTE_TEMPERATURE)
    assert failure.value.code is ErrorCode.TEMPERATURE_KIND_MISMATCH


@pytest.mark.parametrize("value", [1, 2.5, "42", ""])
def test_physical_quantity_requires_explicit_unit(value: object) -> None:
    with pytest.raises(DomainError) as failure:
        parse_quantity(value, PhysicalDimension.LENGTH, path="mesh.minimum_size")
    assert failure.value.as_dict()["code"] == "UNIT_REQUIRED"
    assert failure.value.path == "mesh.minimum_size"


def test_wrong_dimension_is_structured() -> None:
    with pytest.raises(DomainError) as failure:
        parse_quantity("3 s", PhysicalDimension.LENGTH)
    assert failure.value.code is ErrorCode.UNIT_DIMENSION_MISMATCH
    assert failure.value.as_dict()["details"]["expected_unit"] == "m"


def test_existing_quantity_dimension_is_checked() -> None:
    duration = parse_quantity("2 s", PhysicalDimension.TIME)
    assert parse_quantity(duration, PhysicalDimension.TIME) is duration
    with pytest.raises(DomainError, match="Expected length"):
        parse_quantity(duration, PhysicalDimension.LENGTH)


def test_nonfinite_quantity_is_rejected() -> None:
    with pytest.raises(DomainError, match="finite"):
        parse_quantity("nan m", PhysicalDimension.LENGTH)


def test_quantity_from_si_round_trips() -> None:
    quantity = quantity_from_si(math.pi, PhysicalDimension.AREA)
    assert quantity.si_value == pytest.approx(math.pi)
    assert quantity.si_unit == "m^2"


def test_serialized_quantity_cannot_forge_si_normalization() -> None:
    quantity = parse_quantity("10 mm", PhysicalDimension.LENGTH)
    forged = quantity.model_dump(mode="json")
    forged["si_value"] = 999.0
    with pytest.raises(DomainError) as failure:
        parse_quantity(forged, PhysicalDimension.LENGTH)
    assert failure.value.code is ErrorCode.UNIT_NORMALIZATION_MISMATCH
