"""Restricted semantic selector AST and deterministic geometry resolver."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import (
    BodyDescriptor,
    CoordinateFrame,
    FaceDescriptor,
    GeometryGraph,
    SurfaceType,
    Vector3,
    vector_dot,
    vector_norm,
    vector_subtract,
)


class AxisName(StrEnum):
    """Local coordinate axes accepted by directional predicates."""

    LOCAL_X = "local_x"
    LOCAL_Y = "local_y"
    LOCAL_Z = "local_z"


class GlobalAxisName(StrEnum):
    """Axes of the source CAD global coordinate system."""

    X = "x"
    Y = "y"
    Z = "z"


class ExtremeSide(StrEnum):
    """Minimum or maximum coordinate extreme."""

    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class CoordinateSide(StrEnum):
    """Positive or negative side of a local coordinate plane."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


class EntityKind(StrEnum):
    """Entity class selected by a semantic role."""

    BODY = "body"
    FACE = "face"


class Cardinality(StrEnum):
    """Supported role cardinality constraints."""

    EXACTLY_ONE = "exactly_one"
    ZERO_OR_ONE = "zero_or_one"
    ONE_OR_MORE = "one_or_more"
    ZERO_OR_MORE = "zero_or_more"
    EXACTLY_N = "exactly_n"


class RelativeRange(BaseModel):
    """Inclusive range relative to the largest candidate measure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: float = Field(ge=0.0)
    maximum: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ordered(self) -> RelativeRange:
        """Require an ordered range."""

        if self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum.")
        return self


class CentroidSideSpec(BaseModel):
    """Side of a local coordinate plane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis: AxisName
    side: CoordinateSide
    offset: float = 0.0
    tolerance: float = Field(default=1.0e-12, ge=0.0)


class NearestPointSpec(BaseModel):
    """Nearest entity-centroid query in local coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point: Vector3
    tie_tolerance: float = Field(default=1.0e-12, ge=0.0)


class AxisToleranceSpec(BaseModel):
    """Axis alignment with an angular tolerance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis: AxisName
    tolerance_deg: float = Field(default=5.0, ge=0.0, le=90.0)


class CentroidExtremeSpec(BaseModel):
    """Centroid extreme along one local axis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis: AxisName
    side: ExtremeSide
    tolerance_relative: float = Field(default=1.0e-9, ge=0.0)


class BoundingBoxExtremeSpec(BaseModel):
    """Extreme of an exact source-axis-aligned bounding box."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis: GlobalAxisName
    side: ExtremeSide
    tolerance_relative: float = Field(default=1.0e-9, ge=0.0)


class CentroidPercentileSpec(BaseModel):
    """Inclusive normalized centroid-position percentile interval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    axis: AxisName
    minimum: float = Field(ge=0.0, le=100.0)
    maximum: float = Field(ge=0.0, le=100.0)

    @model_validator(mode="after")
    def ordered(self) -> CentroidPercentileSpec:
        """Require an ordered percentile interval."""

        if self.minimum > self.maximum:
            raise ValueError("minimum percentile must not exceed maximum.")
        return self


class AllSelector(BaseModel):
    """Sequential intersection of selector children."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    all: tuple[SelectorExpression, ...] = Field(min_length=1)


class AnySelector(BaseModel):
    """Union of selector children."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    any: tuple[SelectorExpression, ...] = Field(min_length=1)


class NotSelector(BaseModel):
    """Complement of one selector child."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    not_: SelectorExpression = Field(alias="not", serialization_alias="not")


class SolidBodyPredicate(BaseModel):
    """Filter by solid-body status."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    solid_body: bool


class NameRegexPredicate(BaseModel):
    """Filter display names with a regular expression."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name_regex: str = Field(min_length=1, max_length=512)


class NamedSelectionPredicate(BaseModel):
    """Filter entities by exact case-sensitive Ansys Named Selection membership."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    named_selection: str = Field(min_length=1, max_length=256)


class VolumeRelativeRangePredicate(BaseModel):
    """Filter body volume relative to the largest candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    volume_relative_range: RelativeRange


class CentroidSidePredicate(BaseModel):
    """Filter centroid side in the selected coordinate frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    centroid_side: CentroidSideSpec


class LargestVolumePredicate(BaseModel):
    """Select all bodies tied for largest volume."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    largest_volume: bool


class NearestToPointPredicate(BaseModel):
    """Select all entities tied for nearest centroid."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    nearest_to_point: NearestPointSpec


class ExternalOfPredicate(BaseModel):
    """Filter external faces belonging to a resolved body role."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    external_of: str = Field(min_length=1)


class InterfacePredicate(BaseModel):
    """Filter by interface classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    interface: bool


class ParentBodyRolePredicate(BaseModel):
    """Filter faces whose parent belongs to a resolved body role."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    parent_body_role: str = Field(min_length=1)


class SurfaceTypePredicate(BaseModel):
    """Filter face surface classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    surface_type: SurfaceType


class NormalParallelToPredicate(BaseModel):
    """Filter planar normals parallel or antiparallel to a local axis."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    normal_parallel_to: AxisToleranceSpec


class CentroidExtremePredicate(BaseModel):
    """Select all entities tied at a local centroid extreme."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    centroid_extreme: CentroidExtremeSpec


class BoundingBoxExtremePredicate(BaseModel):
    """Select entities tied at an exact source bounding-box extreme."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bounding_box_extreme: BoundingBoxExtremeSpec


class CentroidPercentilePredicate(BaseModel):
    """Filter entities by normalized centroid-position percentile."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    centroid_percentile: CentroidPercentileSpec


class AreaRelativeRangePredicate(BaseModel):
    """Filter face area relative to the largest candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    area_relative_range: RelativeRange


type SelectorNode = (
    AllSelector
    | AnySelector
    | NotSelector
    | SolidBodyPredicate
    | NameRegexPredicate
    | NamedSelectionPredicate
    | VolumeRelativeRangePredicate
    | CentroidSidePredicate
    | LargestVolumePredicate
    | NearestToPointPredicate
    | ExternalOfPredicate
    | InterfacePredicate
    | ParentBodyRolePredicate
    | SurfaceTypePredicate
    | NormalParallelToPredicate
    | CentroidExtremePredicate
    | BoundingBoxExtremePredicate
    | CentroidPercentilePredicate
    | AreaRelativeRangePredicate
)


class SelectorExpression(RootModel[SelectorNode]):
    """One recursively composed, data-only selector expression."""

    model_config = ConfigDict(frozen=True)


AllSelector.model_rebuild()
AnySelector.model_rebuild()
NotSelector.model_rebuild()
SelectorExpression.model_rebuild()


class RoleDefinition(BaseModel):
    """Entity type, cardinality, and selector for one semantic role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: EntityKind
    cardinality: Cardinality
    selector: SelectorExpression
    count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_count(self) -> RoleDefinition:
        """Allow count only for exactly_n and require it there."""

        if self.cardinality is Cardinality.EXACTLY_N and self.count is None:
            raise ValueError("exactly_n cardinality requires count.")
        if self.cardinality is not Cardinality.EXACTLY_N and self.count is not None:
            raise ValueError("count is only valid with exactly_n cardinality.")
        return self


class ResolutionStatus(StrEnum):
    """Outcome of resolving a semantic role."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"


class SelectionEvidence(BaseModel):
    """Why one run-local entity was selected for a semantic role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    semantic_role: str
    stable_key: str
    internal_runtime_id: str
    entity: EntityKind
    selector: dict[str, Any]
    geometry_fingerprint: str
    measurements: dict[str, Any]


class ResolutionError(BaseModel):
    """Structured resolution error embedded in a report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: ErrorCode
    path: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class RoleResolution(BaseModel):
    """Deterministic resolution result for one role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    status: ResolutionStatus
    selected_count: int
    confidence: str = "deterministic"
    candidate_keys: tuple[str, ...] = ()
    evidence: tuple[SelectionEvidence, ...] = ()
    error: ResolutionError | None = None


class RegionResolution(BaseModel):
    """Resolution of all manifest roles against one geometry graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    geometry_fingerprint: str
    roles: dict[str, RoleResolution]

    @property
    def successful(self) -> bool:
        """Return whether every role resolved without ambiguity or capability failure."""

        return all(item.status is ResolutionStatus.RESOLVED for item in self.roles.values())


type Entity = BodyDescriptor | FaceDescriptor


def _entity_key(entity: Entity) -> str:
    return entity.stable_key


def _axis_index(axis: AxisName) -> int:
    return {AxisName.LOCAL_X: 0, AxisName.LOCAL_Y: 1, AxisName.LOCAL_Z: 2}[axis]


def _local_coordinate(entity: Entity, frame: CoordinateFrame, axis: AxisName) -> float:
    if entity.centroid is None:
        raise DomainError(
            ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY,
            "selector.centroid",
            "Exact entity centroid is unavailable for the active Geometry capability tier.",
        )
    return frame.to_local_point(entity.centroid).root[_axis_index(axis)]


def _bounding_coordinate(entity: Entity, axis: GlobalAxisName, side: ExtremeSide) -> float:
    index = {GlobalAxisName.X: 0, GlobalAxisName.Y: 1, GlobalAxisName.Z: 2}[axis]
    point = (
        entity.bounding_box.minimum if side is ExtremeSide.MINIMUM else entity.bounding_box.maximum
    )
    return point.root[index]


def _role_body_keys(role: str, resolved: dict[str, RoleResolution]) -> set[str]:
    item = resolved.get(role)
    if item is None or item.status is not ResolutionStatus.RESOLVED:
        raise DomainError(
            ErrorCode.UNRESOLVED_ROLE,
            f"roles.{role}",
            f"Dependent role {role!r} is not resolved.",
        )
    keys: set[str] = set()
    for evidence in item.evidence:
        parent = evidence.measurements.get("parent_body_key")
        keys.add(str(parent) if parent is not None else evidence.stable_key)
    return keys


def _ensure_bodies(entities: list[Entity], path: str) -> list[BodyDescriptor]:
    if not all(isinstance(entity, BodyDescriptor) for entity in entities):
        raise DomainError(
            ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY,
            path,
            "Body predicate cannot be evaluated for face candidates.",
        )
    return [entity for entity in entities if isinstance(entity, BodyDescriptor)]


def _ensure_faces(entities: list[Entity], path: str) -> list[FaceDescriptor]:
    if not all(isinstance(entity, FaceDescriptor) for entity in entities):
        raise DomainError(
            ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY,
            path,
            "Face predicate cannot be evaluated for body candidates.",
        )
    return [entity for entity in entities if isinstance(entity, FaceDescriptor)]


def _tied_minimum(
    entities: Sequence[Entity], values: Sequence[float], tolerance: float
) -> list[Entity]:
    if not entities:
        return []
    minimum = min(values)
    scale = max(max(abs(value) for value in values), 1.0)
    return [
        entity
        for entity, value in zip(entities, values, strict=True)
        if abs(value - minimum) <= tolerance * scale
    ]


def _apply_selector(
    expression: SelectorExpression,
    candidates: list[Entity],
    frame: CoordinateFrame,
    resolved: dict[str, RoleResolution],
) -> list[Entity]:
    node = expression.root
    if isinstance(node, AllSelector):
        current = candidates
        for child in node.all:
            current = _apply_selector(child, current, frame, resolved)
        return current
    if isinstance(node, AnySelector):
        merged = {
            entity.stable_key: entity
            for child in node.any
            for entity in _apply_selector(child, candidates, frame, resolved)
        }
        return sorted(merged.values(), key=_entity_key)
    if isinstance(node, NotSelector):
        excluded = {
            entity.stable_key for entity in _apply_selector(node.not_, candidates, frame, resolved)
        }
        return [entity for entity in candidates if entity.stable_key not in excluded]
    if isinstance(node, SolidBodyPredicate):
        return [
            body
            for body in _ensure_bodies(candidates, "selector.solid_body")
            if body.solid is node.solid_body
        ]
    if isinstance(node, NameRegexPredicate):
        try:
            pattern = re.compile(node.name_regex)
        except re.error as exc:
            raise DomainError(
                ErrorCode.INVALID_SELECTOR,
                "selector.name_regex",
                f"Invalid regular expression: {exc}.",
            ) from exc
        return [entity for entity in candidates if pattern.search(entity.display_name)]
    if isinstance(node, NamedSelectionPredicate):
        return [entity for entity in candidates if node.named_selection in entity.named_selections]
    if isinstance(node, VolumeRelativeRangePredicate):
        volume_bodies = _ensure_bodies(candidates, "selector.volume_relative_range")
        maximum = max((body.volume.si_value for body in volume_bodies), default=0.0)
        if maximum <= 0.0:
            return []
        bounds = node.volume_relative_range
        volume_selected: list[Entity] = [
            body
            for body in volume_bodies
            if bounds.minimum <= body.volume.si_value / maximum <= bounds.maximum
        ]
        return volume_selected
    if isinstance(node, CentroidSidePredicate):
        side_spec = node.centroid_side
        side_selected: list[Entity] = []
        for entity in candidates:
            coordinate = _local_coordinate(entity, frame, side_spec.axis) - side_spec.offset
            if side_spec.side is CoordinateSide.POSITIVE and coordinate >= -side_spec.tolerance:
                side_selected.append(entity)
            if side_spec.side is CoordinateSide.NEGATIVE and coordinate <= side_spec.tolerance:
                side_selected.append(entity)
        return side_selected
    if isinstance(node, LargestVolumePredicate):
        largest_bodies: list[Entity] = list(_ensure_bodies(candidates, "selector.largest_volume"))
        if not node.largest_volume:
            return largest_bodies
        return _tied_minimum(
            largest_bodies,
            [-body.volume.si_value for body in largest_bodies if isinstance(body, BodyDescriptor)],
            1.0e-12,
        )
    if isinstance(node, NearestToPointPredicate):
        nearest_spec = node.nearest_to_point
        distances = [
            vector_norm(
                vector_subtract(
                    Vector3(
                        (
                            _local_coordinate(entity, frame, AxisName.LOCAL_X),
                            _local_coordinate(entity, frame, AxisName.LOCAL_Y),
                            _local_coordinate(entity, frame, AxisName.LOCAL_Z),
                        )
                    ),
                    nearest_spec.point,
                )
            )
            for entity in candidates
        ]
        return _tied_minimum(candidates, distances, nearest_spec.tie_tolerance)
    if isinstance(node, ExternalOfPredicate):
        faces = _ensure_faces(candidates, "selector.external_of")
        body_keys = _role_body_keys(node.external_of, resolved)
        return [face for face in faces if face.external and face.parent_body_key in body_keys]
    if isinstance(node, InterfacePredicate):
        return [
            face
            for face in _ensure_faces(candidates, "selector.interface")
            if face.interface is node.interface
        ]
    if isinstance(node, ParentBodyRolePredicate):
        faces = _ensure_faces(candidates, "selector.parent_body_role")
        body_keys = _role_body_keys(node.parent_body_role, resolved)
        return [face for face in faces if face.parent_body_key in body_keys]
    if isinstance(node, SurfaceTypePredicate):
        return [
            face
            for face in _ensure_faces(candidates, "selector.surface_type")
            if face.surface_type is node.surface_type
        ]
    if isinstance(node, NormalParallelToPredicate):
        faces = _ensure_faces(candidates, "selector.normal_parallel_to")
        if any(face.normal is None for face in faces):
            raise DomainError(
                ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY,
                "selector.normal_parallel_to",
                "Face normal is unavailable for at least one candidate.",
            )
        target = frame.axes()[_axis_index(node.normal_parallel_to.axis)]
        minimum_alignment = math.cos(math.radians(node.normal_parallel_to.tolerance_deg))
        return [
            face
            for face in faces
            if face.normal is not None
            and abs(vector_dot(face.normal, target))
            / (vector_norm(face.normal) * vector_norm(target))
            >= minimum_alignment
        ]
    if isinstance(node, CentroidExtremePredicate):
        extreme_spec = node.centroid_extreme
        coordinates = [_local_coordinate(entity, frame, extreme_spec.axis) for entity in candidates]
        values = (
            coordinates
            if extreme_spec.side is ExtremeSide.MINIMUM
            else [-value for value in coordinates]
        )
        return _tied_minimum(candidates, values, extreme_spec.tolerance_relative)
    if isinstance(node, BoundingBoxExtremePredicate):
        box_spec = node.bounding_box_extreme
        coordinates = [
            _bounding_coordinate(entity, box_spec.axis, box_spec.side) for entity in candidates
        ]
        values = (
            coordinates
            if box_spec.side is ExtremeSide.MINIMUM
            else [-value for value in coordinates]
        )
        return _tied_minimum(candidates, values, box_spec.tolerance_relative)
    if isinstance(node, CentroidPercentilePredicate):
        percentile_spec = node.centroid_percentile
        coordinates = [
            _local_coordinate(entity, frame, percentile_spec.axis) for entity in candidates
        ]
        if not coordinates:
            return []
        minimum = min(coordinates)
        span = max(coordinates) - minimum
        if span <= 1.0e-15:
            percentiles = [50.0] * len(coordinates)
        else:
            percentiles = [(value - minimum) / span * 100.0 for value in coordinates]
        return [
            entity
            for entity, percentile in zip(candidates, percentiles, strict=True)
            if percentile_spec.minimum <= percentile <= percentile_spec.maximum
        ]
    if isinstance(node, AreaRelativeRangePredicate):
        faces = _ensure_faces(candidates, "selector.area_relative_range")
        maximum = max((face.area.si_value for face in faces), default=0.0)
        if maximum <= 0.0:
            return []
        bounds = node.area_relative_range
        return [
            face
            for face in faces
            if bounds.minimum <= face.area.si_value / maximum <= bounds.maximum
        ]
    raise AssertionError(f"Unhandled selector node: {type(node).__name__}")


def _dependencies(expression: SelectorExpression) -> set[str]:
    node = expression.root
    if isinstance(node, AllSelector):
        return set().union(*(_dependencies(child) for child in node.all))
    if isinstance(node, AnySelector):
        return set().union(*(_dependencies(child) for child in node.any))
    if isinstance(node, NotSelector):
        return _dependencies(node.not_)
    if isinstance(node, ExternalOfPredicate):
        return {node.external_of}
    if isinstance(node, ParentBodyRolePredicate):
        return {node.parent_body_role}
    return set()


def _cardinality_error(
    role: str, definition: RoleDefinition, selected_count: int
) -> tuple[ResolutionStatus, ResolutionError] | None:
    cardinality = definition.cardinality
    expected = definition.count if cardinality is Cardinality.EXACTLY_N else None
    too_few = (
        cardinality is Cardinality.EXACTLY_ONE
        and selected_count < 1
        or cardinality is Cardinality.ONE_OR_MORE
        and selected_count < 1
        or cardinality is Cardinality.EXACTLY_N
        and expected is not None
        and selected_count < expected
    )
    too_many = (
        cardinality in {Cardinality.EXACTLY_ONE, Cardinality.ZERO_OR_ONE}
        and selected_count > 1
        or cardinality is Cardinality.EXACTLY_N
        and expected is not None
        and selected_count > expected
    )
    if not too_few and not too_many:
        return None
    code = ErrorCode.UNRESOLVED_ROLE if too_few else ErrorCode.AMBIGUOUS_ROLE
    status = ResolutionStatus.UNRESOLVED if too_few else ResolutionStatus.AMBIGUOUS
    return status, ResolutionError(
        code=code,
        path=f"roles.{role}",
        message=f"Role {role!r} selected {selected_count} entities for {cardinality.value}.",
        details={"selected_count": selected_count, "expected_count": expected},
    )


def _evidence(
    role: str,
    definition: RoleDefinition,
    entity: Entity,
    fingerprint: str,
) -> SelectionEvidence:
    selector = definition.selector.model_dump(mode="json", by_alias=True)
    assert isinstance(selector, dict)
    if isinstance(entity, BodyDescriptor):
        measurements: dict[str, Any] = {
            "volume_m3": entity.volume.si_value,
            "surface_area_m2": entity.surface_area.si_value,
            "centroid_m": None if entity.centroid is None else list(entity.centroid.root),
            "entity_type": entity.entity_type.value,
            "named_selections": list(entity.named_selections),
        }
    else:
        measurements = {
            "area_m2": entity.area.si_value,
            "centroid_m": None if entity.centroid is None else list(entity.centroid.root),
            "surface_type": entity.surface_type.value,
            "normal": None if entity.normal is None else list(entity.normal.root),
            "axis": None if entity.axis is None else list(entity.axis.root),
            "bounding_box_m": {
                "minimum": list(entity.bounding_box.minimum.root),
                "maximum": list(entity.bounding_box.maximum.root),
            },
            "parent_body_key": entity.parent_body_key,
            "external": entity.external,
            "interface": entity.interface,
            "named_selections": list(entity.named_selections),
        }
    return SelectionEvidence(
        semantic_role=role,
        stable_key=entity.stable_key,
        internal_runtime_id=entity.internal_runtime_id,
        entity=definition.entity,
        selector=selector,
        geometry_fingerprint=fingerprint,
        measurements=measurements,
    )


def resolve_regions(
    graph: GeometryGraph,
    roles: dict[str, RoleDefinition],
    frame: CoordinateFrame | None = None,
) -> RegionResolution:
    """Resolve manifest roles without arbitrary tie-breaking or code evaluation."""

    active_frame = frame or CoordinateFrame()
    fingerprint = graph.fingerprint()
    resolved: dict[str, RoleResolution] = {}
    visiting: set[str] = set()

    def resolve_role(role: str) -> RoleResolution:
        if role in resolved:
            return resolved[role]
        definition = roles.get(role)
        if definition is None:
            raise DomainError(
                ErrorCode.UNKNOWN_ROLE,
                f"roles.{role}",
                f"Selector references unknown role {role!r}.",
            )
        if role in visiting:
            raise DomainError(
                ErrorCode.ROLE_DEPENDENCY_CYCLE,
                f"roles.{role}",
                f"Role dependency cycle includes {role!r}.",
            )
        visiting.add(role)
        for dependency in sorted(_dependencies(definition.selector)):
            resolve_role(dependency)
        candidates: list[Entity]
        candidates = list(graph.bodies if definition.entity is EntityKind.BODY else graph.faces)
        candidates.sort(key=_entity_key)
        try:
            selected = _apply_selector(definition.selector, candidates, active_frame, resolved)
        except DomainError as exc:
            result = RoleResolution(
                role=role,
                status=ResolutionStatus.UNSUPPORTED,
                selected_count=0,
                error=ResolutionError(**exc.as_dict()),
            )
        else:
            selected = sorted(
                {item.stable_key: item for item in selected}.values(), key=_entity_key
            )
            violation = _cardinality_error(role, definition, len(selected))
            if violation is None:
                result = RoleResolution(
                    role=role,
                    status=ResolutionStatus.RESOLVED,
                    selected_count=len(selected),
                    candidate_keys=tuple(item.stable_key for item in selected),
                    evidence=tuple(
                        _evidence(role, definition, item, fingerprint) for item in selected
                    ),
                )
            else:
                status, error = violation
                result = RoleResolution(
                    role=role,
                    status=status,
                    selected_count=len(selected),
                    candidate_keys=tuple(item.stable_key for item in selected),
                    error=error,
                )
        visiting.remove(role)
        resolved[role] = result
        return result

    for role in sorted(roles):
        resolve_role(role)
    return RegionResolution(
        geometry_fingerprint=fingerprint,
        roles={role: resolved[role] for role in sorted(resolved)},
    )


def selector_json(expression: SelectorExpression) -> str:
    """Serialize a selector deterministically for evidence and hashing."""

    return json.dumps(
        expression.model_dump(mode="json", by_alias=True),
        separators=(",", ":"),
        sort_keys=True,
    )
