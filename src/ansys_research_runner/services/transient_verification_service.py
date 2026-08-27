"""Independent transient thermal reference and time-series consistency checks."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import ConvectionBoundary
from ansys_research_runner.domain.results import (
    PhysicalVerificationReport,
    TransientThermalObservation,
    VerificationMetric,
    VerificationStatus,
)
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile


class LumpedCapacitanceReference(BaseModel):
    """Semantic roles and documented tolerances for a lumped-capacitance check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thermal_domain_role: str = "thermal_domain"
    convection_region: str = "exterior"
    maximum_biot_number: float = Field(default=0.1, gt=0.0)
    maximum_absolute_error_K: float = Field(default=0.5, gt=0.0)
    maximum_rmse_K: float = Field(default=0.25, gt=0.0)


def _error_metric(name: str, observed_error_K: float, tolerance_K: float) -> VerificationMetric:
    return VerificationMetric(
        name=name,
        expected=0.0,
        observed=observed_error_K,
        unit="K",
        absolute_error=abs(observed_error_K),
        absolute_tolerance=tolerance_K,
        status=(
            VerificationStatus.PASSED
            if abs(observed_error_K) <= tolerance_K
            else VerificationStatus.FAILED
        ),
    )


def verify_lumped_capacitance(
    *,
    cae_ir: ResolvedCAEIR,
    graph: GeometryGraph,
    observation: TransientThermalObservation,
    reference: LumpedCapacitanceReference | None = None,
) -> PhysicalVerificationReport:
    """Compare volume-average temperature frames to the independent lumped solution."""

    reference = reference or LumpedCapacitanceReference()
    material = cae_ir.materials.get(reference.thermal_domain_role)
    convection = next(
        (
            boundary
            for boundary in cae_ir.boundary_conditions
            if isinstance(boundary, ConvectionBoundary)
            and boundary.region == reference.convection_region
        ),
        None,
    )
    initial = cae_ir.analysis_settings.initial_temperature
    if (
        material is None
        or material.density is None
        or material.specific_heat is None
        or convection is None
        or initial is None
    ):
        return PhysicalVerificationReport(
            reference="lumped_capacitance",
            status=VerificationStatus.NOT_RUN,
            reason="Density, specific heat, initial temperature, and convection are required.",
        )
    body_keys = {
        item.stable_key for item in cae_ir.selection_evidence.get(reference.thermal_domain_role, ())
    }
    face_keys = {
        item.stable_key for item in cae_ir.selection_evidence.get(reference.convection_region, ())
    }
    volume = sum(body.volume.si_value for body in graph.bodies if body.stable_key in body_keys)
    area = sum(face.area.si_value for face in graph.faces if face.stable_key in face_keys)
    if volume <= 0.0 or area <= 0.0:
        return PhysicalVerificationReport(
            reference="lumped_capacitance",
            status=VerificationStatus.NOT_RUN,
            reason="Resolved positive body volume and convection area are required.",
        )
    characteristic_length = volume / area
    biot = (
        convection.film_coefficient.si_value
        * characteristic_length
        / material.thermal_conductivity.si_value
    )
    if biot > reference.maximum_biot_number:
        return PhysicalVerificationReport(
            reference="lumped_capacitance",
            status=VerificationStatus.INCONCLUSIVE,
            checks={"biot_number": VerificationStatus.FAILED},
            reason=(
                f"Biot number {biot:.17g} exceeds the configured lumped limit "
                f"{reference.maximum_biot_number:.17g}."
            ),
        )
    decay = (
        convection.film_coefficient.si_value
        * area
        / (material.density.si_value * material.specific_heat.si_value * volume)
    )
    ambient_K = convection.ambient_temperature.si_value
    initial_K = initial.si_value
    expected = tuple(
        ambient_K + (initial_K - ambient_K) * math.exp(-decay * time_s)
        for time_s in observation.times_s
    )
    errors = tuple(
        observed - reference_value
        for observed, reference_value in zip(
            observation.volume_average_temperature_K,
            expected,
            strict=True,
        )
    )
    maximum_error = max(abs(error) for error in errors)
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
    point_metrics = tuple(
        VerificationMetric(
            name=f"volume_average_temperature_at_{time_s:.17g}_s",
            expected=expected_K,
            observed=observed_K,
            unit="K",
            absolute_error=abs(observed_K - expected_K),
            absolute_tolerance=reference.maximum_absolute_error_K,
            status=(
                VerificationStatus.PASSED
                if abs(observed_K - expected_K) <= reference.maximum_absolute_error_K
                else VerificationStatus.FAILED
            ),
        )
        for time_s, expected_K, observed_K in zip(
            observation.times_s,
            expected,
            observation.volume_average_temperature_K,
            strict=True,
        )
    )
    metrics = (
        *point_metrics,
        _error_metric("maximum_absolute_error", maximum_error, reference.maximum_absolute_error_K),
        _error_metric("rmse", rmse, reference.maximum_rmse_K),
    )
    status = (
        VerificationStatus.PASSED
        if all(metric.status is VerificationStatus.PASSED for metric in metrics)
        else VerificationStatus.FAILED
    )
    return PhysicalVerificationReport(
        reference="lumped_capacitance",
        status=status,
        metrics=metrics,
        checks={
            "biot_number": VerificationStatus.PASSED,
            "time_series_alignment": VerificationStatus.PASSED,
        },
        assumptions=(
            f"Biot number={biot:.17g}",
            "uniform body temperature",
            "constant material properties and film coefficient",
        ),
    )


def verify_profile_time_alignment(
    *,
    profile: ResolvedHeatGenerationProfile,
    observation: TransientThermalObservation,
    expected_end_time_s: float,
) -> PhysicalVerificationReport:
    """Verify profile/analysis end time and transient field-frame alignment."""

    tolerance = max(1.0e-9, abs(expected_end_time_s) * 1.0e-12)
    profile_end_matches = math.isclose(
        profile.points[-1].time_s,
        expected_end_time_s,
        rel_tol=0.0,
        abs_tol=tolerance,
    )
    observation_end_matches = math.isclose(
        observation.times_s[-1],
        expected_end_time_s,
        rel_tol=0.0,
        abs_tol=tolerance,
    )
    checks = {
        "profile_end_time": (
            VerificationStatus.PASSED if profile_end_matches else VerificationStatus.FAILED
        ),
        "result_end_time": (
            VerificationStatus.PASSED if observation_end_matches else VerificationStatus.FAILED
        ),
        "field_frame_count": (
            VerificationStatus.PASSED
            if len(observation.times_s) == len(observation.volume_average_temperature_K)
            else VerificationStatus.FAILED
        ),
    }
    status = (
        VerificationStatus.PASSED
        if all(item is VerificationStatus.PASSED for item in checks.values())
        else VerificationStatus.FAILED
    )
    return PhysicalVerificationReport(
        reference="time_series_heat_generation_profile",
        status=status,
        checks=checks,
        assumptions=("profile interpolation is owned by the solver adapter",),
    )
