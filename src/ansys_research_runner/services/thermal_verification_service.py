"""Independent analytic and conservation checks for supported thermal cases."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import (
    FaceDescriptor,
    GeometryGraph,
    Vector3,
    vector_dot,
    vector_subtract,
)
from ansys_research_runner.domain.recipe import (
    ConvectionBoundary,
    TemperatureBoundary,
    VolumetricHeatLoad,
)
from ansys_research_runner.domain.results import (
    PhysicalVerificationReport,
    ThermalObservation,
    VerificationMetric,
    VerificationStatus,
)


class SteadyConductionReference(BaseModel):
    """Semantic configuration and tolerances for a one-dimensional conduction check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thermal_domain_role: str = "thermal_domain"
    cold_region: str = "cold_boundary"
    hot_region: str = "hot_boundary"
    center_probe_name: str = "center"
    boundary_temperature_tolerance_K: float = Field(default=0.25, gt=0.0)
    center_temperature_tolerance_K: float = Field(default=0.5, gt=0.0)
    heat_rate_relative_tolerance: float = Field(default=0.03, gt=0.0)
    energy_balance_relative_tolerance: float = Field(default=0.01, gt=0.0)


class UniformGenerationConvectionReference(BaseModel):
    """Semantic configuration for generation/convection conservation checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thermal_domain_role: str = "thermal_domain"
    heat_source_region: str = "thermal_domain"
    convection_region: str = "exterior"
    heat_balance_relative_tolerance: float = Field(default=0.03, gt=0.0)


def _metric(
    *,
    name: str,
    expected: float,
    observed: float,
    unit: str,
    absolute_tolerance: float | None = None,
    relative_tolerance: float | None = None,
) -> VerificationMetric:
    absolute_error = abs(observed - expected)
    relative_error = None if expected == 0.0 else absolute_error / abs(expected)
    allowed = max(
        absolute_tolerance or 0.0,
        abs(expected) * (relative_tolerance or 0.0),
    )
    return VerificationMetric(
        name=name,
        expected=expected,
        observed=observed,
        unit=unit,
        absolute_error=absolute_error,
        relative_error=relative_error,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
        status=(
            VerificationStatus.PASSED if absolute_error <= allowed else VerificationStatus.FAILED
        ),
    )


def _selected_keys(cae_ir: ResolvedCAEIR, role: str) -> tuple[str, ...]:
    evidence = cae_ir.selection_evidence.get(role, ())
    return tuple(item.stable_key for item in evidence)


def _faces(graph: GeometryGraph, keys: tuple[str, ...]) -> tuple[FaceDescriptor, ...]:
    requested = set(keys)
    return tuple(face for face in graph.faces if face.stable_key in requested)


def _face_reference_point(face: FaceDescriptor) -> tuple[float, float, float]:
    """Return an exact centroid or an explicitly bounded plane reference point."""

    if face.centroid is not None:
        return face.centroid.root
    coordinates = tuple(
        (low + high) / 2.0
        for low, high in zip(
            face.bounding_box.minimum.root,
            face.bounding_box.maximum.root,
            strict=True,
        )
    )
    return (coordinates[0], coordinates[1], coordinates[2])


def _average_face_reference(faces: tuple[FaceDescriptor, ...]) -> tuple[float, float, float]:
    total_area = sum(face.area.si_value for face in faces)
    if total_area <= 0.0:
        raise ValueError("Selected reference faces require positive aggregate area.")
    references = [_face_reference_point(face) for face in faces]
    reference = tuple(
        sum(
            point[index] * face.area.si_value for point, face in zip(references, faces, strict=True)
        )
        / total_area
        for index in range(3)
    )
    return reference[0], reference[1], reference[2]


def verify_steady_conduction(
    *,
    cae_ir: ResolvedCAEIR,
    graph: GeometryGraph,
    observation: ThermalObservation,
    reference: SteadyConductionReference | None = None,
) -> PhysicalVerificationReport:
    """Compare a constant-section conduction result with its independent 1D solution."""

    reference = reference or SteadyConductionReference()

    temperature_boundaries = {
        condition.region: condition
        for condition in cae_ir.boundary_conditions
        if isinstance(condition, TemperatureBoundary)
    }
    cold = temperature_boundaries.get(reference.cold_region)
    hot = temperature_boundaries.get(reference.hot_region)
    if cold is None or hot is None:
        return PhysicalVerificationReport(
            reference="one_dimensional_steady_conduction",
            status=VerificationStatus.NOT_RUN,
            reason="Both configured prescribed-temperature regions are required.",
        )
    cold_faces = _faces(graph, _selected_keys(cae_ir, reference.cold_region))
    hot_faces = _faces(graph, _selected_keys(cae_ir, reference.hot_region))
    if not cold_faces or not hot_faces:
        return PhysicalVerificationReport(
            reference="one_dimensional_steady_conduction",
            status=VerificationStatus.NOT_RUN,
            reason="Resolved hot and cold faces are required for the analytic reference.",
        )
    cold_centroid = _average_face_reference(cold_faces)
    hot_centroid = _average_face_reference(hot_faces)
    local_x = cae_ir.coordinate_frame.axes()[0]
    separation = abs(
        vector_dot(
            vector_subtract(
                graph_vector(hot_centroid),
                graph_vector(cold_centroid),
            ),
            local_x,
        )
    )
    if separation <= 0.0:
        return PhysicalVerificationReport(
            reference="one_dimensional_steady_conduction",
            status=VerificationStatus.NOT_RUN,
            reason="Hot and cold reference planes have zero local-X separation.",
        )
    cross_section_area = (
        sum(face.area.si_value for face in cold_faces)
        + sum(face.area.si_value for face in hot_faces)
    ) / 2.0
    material = cae_ir.materials.get(reference.thermal_domain_role)
    if material is None:
        return PhysicalVerificationReport(
            reference="one_dimensional_steady_conduction",
            status=VerificationStatus.NOT_RUN,
            reason="Thermal-domain material is unavailable.",
        )
    cold_K = cold.value.si_value
    hot_K = hot.value.si_value
    expected_center_K = (cold_K + hot_K) / 2.0
    expected_heat_W = (
        material.thermal_conductivity.si_value
        * cross_section_area
        * abs(hot_K - cold_K)
        / separation
    )
    probes = {probe.name: probe for probe in observation.probes}
    center = probes.get(reference.center_probe_name)
    required_values = (
        observation.boundary_temperatures_K.get(reference.cold_region),
        observation.boundary_temperatures_K.get(reference.hot_region),
        None if center is None else center.value_K,
        observation.heat_input_W,
        observation.heat_output_W,
    )
    if any(value is None for value in required_values):
        return PhysicalVerificationReport(
            reference="one_dimensional_steady_conduction",
            status=VerificationStatus.INCONCLUSIVE,
            reason="Solver output omitted a required boundary, center, or heat-rate observation.",
            assumptions=("constant isotropic conductivity", "adiabatic remaining faces"),
        )
    cold_observed, hot_observed, center_observed, heat_input, heat_output = (
        float(value) for value in required_values if value is not None
    )
    metrics = (
        _metric(
            name="cold_boundary_temperature",
            expected=cold_K,
            observed=cold_observed,
            unit="K",
            absolute_tolerance=reference.boundary_temperature_tolerance_K,
        ),
        _metric(
            name="hot_boundary_temperature",
            expected=hot_K,
            observed=hot_observed,
            unit="K",
            absolute_tolerance=reference.boundary_temperature_tolerance_K,
        ),
        _metric(
            name="center_temperature",
            expected=expected_center_K,
            observed=center_observed,
            unit="K",
            absolute_tolerance=reference.center_temperature_tolerance_K,
        ),
        _metric(
            name="analytic_heat_rate",
            expected=expected_heat_W,
            observed=abs(heat_input),
            unit="W",
            relative_tolerance=reference.heat_rate_relative_tolerance,
        ),
        _metric(
            name="energy_balance",
            expected=abs(heat_input),
            observed=abs(heat_output),
            unit="W",
            relative_tolerance=reference.energy_balance_relative_tolerance,
        ),
    )
    status = (
        VerificationStatus.PASSED
        if all(metric.status is VerificationStatus.PASSED for metric in metrics)
        else VerificationStatus.FAILED
    )
    return PhysicalVerificationReport(
        reference="one_dimensional_steady_conduction",
        status=status,
        metrics=metrics,
        checks={"energy_balance": metrics[-1].status},
        assumptions=(
            "constant isotropic conductivity",
            "constant cross-section between the prescribed-temperature planes",
            "adiabatic remaining faces",
        ),
    )


def graph_vector(value: tuple[float, float, float]) -> Vector3:
    """Build a graph vector without accepting arbitrary-length sequences."""

    return Vector3(value)


def verify_uniform_generation_convection(
    *,
    cae_ir: ResolvedCAEIR,
    graph: GeometryGraph,
    observation: ThermalObservation,
    reference: UniformGenerationConvectionReference | None = None,
) -> PhysicalVerificationReport:
    """Check temperature ordering and generated-versus-rejected heat conservation."""

    reference = reference or UniformGenerationConvectionReference()

    generation = next(
        (
            load
            for load in cae_ir.loads
            if isinstance(load, VolumetricHeatLoad) and load.region == reference.heat_source_region
        ),
        None,
    )
    convection = next(
        (
            boundary
            for boundary in cae_ir.boundary_conditions
            if isinstance(boundary, ConvectionBoundary)
            and boundary.region == reference.convection_region
        ),
        None,
    )
    body_keys = set(_selected_keys(cae_ir, reference.thermal_domain_role))
    volume = sum(body.volume.si_value for body in graph.bodies if body.stable_key in body_keys)
    if generation is None or convection is None or volume <= 0.0:
        return PhysicalVerificationReport(
            reference="uniform_generation_external_convection",
            status=VerificationStatus.NOT_RUN,
            reason=(
                "Generation load, convection boundary, and resolved positive volume are required."
            ),
        )
    expected_generation_W = generation.value.si_value * volume
    observed_generation = observation.heat_generation_W
    observed_rejection = observation.heat_rejection_W
    average_K = observation.summary.temperature.volume_average_K
    if observed_generation is None or observed_rejection is None or average_K is None:
        return PhysicalVerificationReport(
            reference="uniform_generation_external_convection",
            status=VerificationStatus.INCONCLUSIVE,
            reason="Solver output omitted generation, rejection, or volume-average temperature.",
        )
    metrics = (
        _metric(
            name="reported_total_generation",
            expected=expected_generation_W,
            observed=abs(observed_generation),
            unit="W",
            relative_tolerance=reference.heat_balance_relative_tolerance,
        ),
        _metric(
            name="generation_rejection_balance",
            expected=abs(observed_generation),
            observed=abs(observed_rejection),
            unit="W",
            relative_tolerance=reference.heat_balance_relative_tolerance,
        ),
    )
    ordering_passed = (
        observation.summary.temperature.maximum_K
        > average_K
        > convection.ambient_temperature.si_value
    )
    checks = {
        "temperature_ordering": (
            VerificationStatus.PASSED if ordering_passed else VerificationStatus.FAILED
        ),
        "energy_balance": metrics[-1].status,
    }
    status = (
        VerificationStatus.PASSED
        if ordering_passed and all(metric.status is VerificationStatus.PASSED for metric in metrics)
        else VerificationStatus.FAILED
    )
    return PhysicalVerificationReport(
        reference="uniform_generation_external_convection",
        status=status,
        metrics=metrics,
        checks=checks,
        assumptions=(
            "steady state",
            "uniform volumetric heat generation",
            "all rejected heat crosses the configured convection region",
        ),
    )
