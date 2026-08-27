"""Cross-contract validation for thermal run compilation."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import (
    BodyAverageTemperatureOutput,
    RegionAverageTemperatureOutput,
    RunRecipe,
)
from ansys_research_runner.domain.selectors import (
    RegionResolution,
    ResolutionStatus,
    RoleDefinition,
)


class ValidationSeverity(StrEnum):
    """Severity of a preflight validation issue."""

    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """One stable validation finding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    path: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Complete preflight result for a compiled run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    issues: tuple[ValidationIssue, ...] = ()


def validate_run_contracts(
    blueprint: AnalysisBlueprint,
    manifest_roles: dict[str, RoleDefinition],
    recipe: RunRecipe,
    graph: GeometryGraph,
    resolution: RegionResolution,
) -> ValidationReport:
    """Validate relationships that no individual Pydantic model can prove alone."""

    issues: list[ValidationIssue] = []

    def add(code: str, path: str, message: str, **details: Any) -> None:
        issues.append(ValidationIssue(code=code, path=path, message=message, details=details))

    if recipe.run.blueprint.id != blueprint.blueprint.id or (
        recipe.run.blueprint.version != blueprint.blueprint.version
    ):
        add(
            "BLUEPRINT_MISMATCH",
            "run.blueprint",
            "Recipe Blueprint reference does not match the loaded Blueprint.",
            recipe=recipe.run.blueprint.compact(),
            loaded=f"{blueprint.blueprint.id}@{blueprint.blueprint.version}",
        )
    if len(graph.bodies) < blueprint.supported_geometry.minimum_bodies:
        add(
            "GEOMETRY_BODY_COUNT_UNSUPPORTED",
            "model",
            "Geometry has fewer bodies than the Blueprint requires.",
            actual=len(graph.bodies),
            minimum=blueprint.supported_geometry.minimum_bodies,
        )
    for role in blueprint.roles.required:
        if role not in manifest_roles:
            add("REQUIRED_ROLE_MISSING", f"roles.{role}", f"Required role {role!r} is missing.")
            continue
        result = resolution.roles.get(role)
        if result is None or result.status is not ResolutionStatus.RESOLVED:
            add(
                "REQUIRED_ROLE_UNRESOLVED",
                f"roles.{role}",
                f"Required role {role!r} did not resolve.",
                status=None if result is None else result.status.value,
            )
    for role, material in recipe.materials.items():
        if role not in manifest_roles:
            add(
                "MATERIAL_ROLE_UNKNOWN",
                f"materials.{role}",
                f"Material assignment references unknown role {role!r}.",
            )
        role_resolution = resolution.roles.get(role)
        if role_resolution is None or role_resolution.status is not ResolutionStatus.RESOLVED:
            add(
                "MATERIAL_ROLE_UNRESOLVED",
                f"materials.{role}",
                f"Material role {role!r} must resolve before compilation.",
            )
        if material.thermal_conductivity.si_value <= 0.0:
            add(
                "MATERIAL_VALUE_INVALID",
                f"materials.{role}.thermal_conductivity",
                "Thermal conductivity must be positive.",
            )
        for property_name in blueprint.materials.required:
            if getattr(material, property_name.value) is None:
                add(
                    "MATERIAL_PROPERTY_MISSING",
                    f"materials.{role}.{property_name.value}",
                    f"Blueprint requires material property {property_name.value!r}.",
                )
        for scalar_property in ("density", "specific_heat"):
            value = getattr(material, scalar_property)
            if value is not None and value.si_value <= 0.0:
                add(
                    "MATERIAL_VALUE_INVALID",
                    f"materials.{role}.{scalar_property}",
                    f"{scalar_property} must be positive.",
                )
    if "thermal_domain" in blueprint.roles.required and "thermal_domain" not in recipe.materials:
        add(
            "MATERIAL_ASSIGNMENT_MISSING",
            "materials.thermal_domain",
            "The thermal_domain role requires a material assignment.",
        )
    for index, condition in enumerate(recipe.boundary_conditions):
        if condition.region not in manifest_roles:
            add(
                "BOUNDARY_ROLE_UNKNOWN",
                f"boundary_conditions[{index}].region",
                f"Boundary condition references unknown role {condition.region!r}.",
            )
        else:
            role_resolution = resolution.roles.get(condition.region)
            if role_resolution is None or (role_resolution.status is not ResolutionStatus.RESOLVED):
                add(
                    "BOUNDARY_ROLE_UNRESOLVED",
                    f"boundary_conditions[{index}].region",
                    f"Boundary role {condition.region!r} must resolve before compilation.",
                )
    for index, output in enumerate(recipe.outputs):
        supported_outputs = {item.value for item in blueprint.outputs.supported}
        if output.type not in supported_outputs:
            add(
                "OUTPUT_UNSUPPORTED",
                f"outputs[{index}].type",
                f"Blueprint does not support output {output.type!r}.",
            )
        if (
            isinstance(
                output,
                (BodyAverageTemperatureOutput, RegionAverageTemperatureOutput),
            )
            and output.region not in manifest_roles
        ):
            add(
                "OUTPUT_ROLE_UNKNOWN",
                f"outputs[{index}].region",
                f"Output references unknown role {output.region!r}.",
            )
        elif isinstance(
            output,
            (BodyAverageTemperatureOutput, RegionAverageTemperatureOutput),
        ):
            role_resolution = resolution.roles.get(output.region)
            if role_resolution is None or (role_resolution.status is not ResolutionStatus.RESOLVED):
                add(
                    "OUTPUT_ROLE_UNRESOLVED",
                    f"outputs[{index}].region",
                    f"Output role {output.region!r} must resolve before compilation.",
                )
    blueprint_is_transient = blueprint.blueprint.id == "solid_transient_thermal"
    if blueprint_is_transient != (recipe.analysis.type == "transient"):
        add(
            "ANALYSIS_TYPE_MISMATCH",
            "analysis.type",
            "Recipe analysis type does not match its Blueprint.",
        )
    ordered = tuple(sorted(issues, key=lambda item: (item.path, item.code)))
    return ValidationReport(valid=not ordered, issues=ordered)
