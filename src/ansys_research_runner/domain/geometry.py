"""Solver-neutral geometry graph and coordinate-frame contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.units import Area, Length, PhysicalQuantity, Volume

_FRAME_TOLERANCE = 1.0e-9


class Vector3(RootModel[tuple[float, float, float]]):
    """Finite three-dimensional vector serialized as a JSON array."""

    model_config = ConfigDict(frozen=True)

    @model_validator(mode="after")
    def finite(self) -> Self:
        """Reject non-finite vector coordinates."""

        if not all(math.isfinite(value) for value in self.root):
            raise ValueError("Vector components must be finite.")
        return self

    @property
    def x(self) -> float:
        """Return the x component."""

        return self.root[0]

    @property
    def y(self) -> float:
        """Return the y component."""

        return self.root[1]

    @property
    def z(self) -> float:
        """Return the z component."""

        return self.root[2]


ZERO = Vector3((0.0, 0.0, 0.0))
X_AXIS = Vector3((1.0, 0.0, 0.0))
Y_AXIS = Vector3((0.0, 1.0, 0.0))
Z_AXIS = Vector3((0.0, 0.0, 1.0))


def vector_from_iterable(values: Iterable[float]) -> Vector3:
    """Build a typed vector from exactly three iterable values."""

    x, y, z = values
    return Vector3((float(x), float(y), float(z)))


def vector_add(left: Vector3, right: Vector3) -> Vector3:
    """Add two vectors."""

    return Vector3((left.x + right.x, left.y + right.y, left.z + right.z))


def vector_subtract(left: Vector3, right: Vector3) -> Vector3:
    """Subtract two vectors."""

    return Vector3((left.x - right.x, left.y - right.y, left.z - right.z))


def vector_scale(vector: Vector3, factor: float) -> Vector3:
    """Scale a vector."""

    return Vector3((vector.x * factor, vector.y * factor, vector.z * factor))


def vector_dot(left: Vector3, right: Vector3) -> float:
    """Return the vector dot product."""

    return sum(a * b for a, b in zip(left.root, right.root, strict=True))


def vector_cross(left: Vector3, right: Vector3) -> Vector3:
    """Return the right-handed vector cross product."""

    return Vector3(
        (
            left.y * right.z - left.z * right.y,
            left.z * right.x - left.x * right.z,
            left.x * right.y - left.y * right.x,
        )
    )


def vector_norm(vector: Vector3) -> float:
    """Return Euclidean vector magnitude."""

    return math.sqrt(vector_dot(vector, vector))


def vector_normalize(vector: Vector3) -> Vector3:
    """Return a unit vector or fail for a zero-length input."""

    norm = vector_norm(vector)
    if norm <= _FRAME_TOLERANCE:
        raise DomainError(
            ErrorCode.INVALID_COORDINATE_FRAME,
            "coordinate_frame",
            "Coordinate-frame axes must have nonzero length.",
        )
    return vector_scale(vector, 1.0 / norm)


class BoundingBox(BaseModel):
    """Axis-aligned bounding box in SI model coordinates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum: Vector3
    maximum: Vector3

    @model_validator(mode="after")
    def ordered(self) -> Self:
        """Ensure every minimum component is no greater than its maximum."""

        if any(low > high for low, high in zip(self.minimum.root, self.maximum.root, strict=True)):
            raise ValueError("Bounding-box minimum must not exceed maximum.")
        return self

    @property
    def diagonal(self) -> float:
        """Return the bounding-box diagonal in meters."""

        return vector_norm(vector_subtract(self.maximum, self.minimum))


class CoordinateFrameType(StrEnum):
    """Supported model-frame sources."""

    CAD_GLOBAL = "cad_global"
    EXPLICIT = "explicit"
    PRINCIPAL = "principal"


class CoordinateFrame(BaseModel):
    """Model coordinate frame used by direction-dependent selectors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: CoordinateFrameType = CoordinateFrameType.CAD_GLOBAL
    origin: Vector3 = ZERO
    x_axis: Vector3 | None = None
    y_axis: Vector3 | None = None
    z_axis: Vector3 | None = None

    @model_validator(mode="after")
    def validate_axes(self) -> Self:
        """Require and validate a right-handed orthogonal explicit frame."""

        axes = (self.x_axis, self.y_axis, self.z_axis)
        if self.type is CoordinateFrameType.CAD_GLOBAL:
            if any(axis is not None for axis in axes):
                raise ValueError("cad_global frame must not redefine axes.")
            return self
        if self.type is CoordinateFrameType.PRINCIPAL:
            if any(axis is not None for axis in axes):
                raise ValueError("principal frame axes are resolved from geometry.")
            return self
        if self.x_axis is None or self.y_axis is None:
            raise ValueError("explicit frame requires x_axis and y_axis.")
        x_axis = vector_normalize(self.x_axis)
        y_axis = vector_normalize(self.y_axis)
        if abs(vector_dot(x_axis, y_axis)) > _FRAME_TOLERANCE:
            raise ValueError("explicit frame x_axis and y_axis must be orthogonal.")
        derived_z = vector_normalize(vector_cross(x_axis, y_axis))
        if self.z_axis is not None:
            supplied_z = vector_normalize(self.z_axis)
            if vector_dot(derived_z, supplied_z) < 1.0 - _FRAME_TOLERANCE:
                raise ValueError("explicit frame axes must be right-handed and orthogonal.")
        return self

    def axes(self) -> tuple[Vector3, Vector3, Vector3]:
        """Return normalized basis axes for a resolved frame."""

        if self.type is CoordinateFrameType.CAD_GLOBAL:
            return X_AXIS, Y_AXIS, Z_AXIS
        if self.type is CoordinateFrameType.PRINCIPAL:
            raise DomainError(
                ErrorCode.AMBIGUOUS_COORDINATE_FRAME,
                "coordinate_frame",
                "Principal frame must be resolved against geometry before use.",
            )
        assert self.x_axis is not None
        assert self.y_axis is not None
        x_axis = vector_normalize(self.x_axis)
        y_axis = vector_normalize(self.y_axis)
        z_axis = vector_normalize(self.z_axis or vector_cross(x_axis, y_axis))
        return x_axis, y_axis, z_axis

    def to_local_point(self, point: Vector3) -> Vector3:
        """Transform a global point into this frame's local coordinates."""

        offset = vector_subtract(point, self.origin)
        x_axis, y_axis, z_axis = self.axes()
        return Vector3(
            (
                vector_dot(offset, x_axis),
                vector_dot(offset, y_axis),
                vector_dot(offset, z_axis),
            )
        )

    def to_local_direction(self, direction: Vector3) -> Vector3:
        """Transform a global direction into this frame without translation."""

        x_axis, y_axis, z_axis = self.axes()
        return Vector3(
            (
                vector_dot(direction, x_axis),
                vector_dot(direction, y_axis),
                vector_dot(direction, z_axis),
            )
        )


class EntityType(StrEnum):
    """Geometry entity types exposed by the graph."""

    SOLID = "solid"


class GeometryCapabilityTier(StrEnum):
    """Geometry evidence level used to construct a graph."""

    FULL_SEMANTIC = "full_semantic"
    SOURCE_BOUND_EXACT = "source_bound_exact"


class SurfaceType(StrEnum):
    """Surface classifications available to v0.x selectors."""

    PLANAR = "planar"
    CYLINDRICAL = "cylindrical"
    CONICAL = "conical"
    SPHERICAL = "spherical"
    OTHER = "other"
    UNKNOWN = "unknown"


class BodyDescriptor(BaseModel):
    """One solid-body descriptor detached from a live solver object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_key: str = Field(min_length=1)
    internal_runtime_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    entity_type: EntityType = EntityType.SOLID
    solid: bool = True
    volume: Volume
    surface_area: Area
    centroid: Vector3 | None
    bounding_box: BoundingBox
    principal_dimensions: tuple[Length, Length, Length]
    principal_axes: tuple[Vector3, Vector3, Vector3] | None = None
    named_selections: tuple[str, ...] = ()
    source_path: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_positive_measures(self) -> Self:
        """Reject nonphysical solid measures."""

        if not self.solid:
            raise ValueError("v0.x Geometry Graph supports only solid bodies.")
        if self.volume.si_value <= 0.0 or self.surface_area.si_value <= 0.0:
            raise ValueError("Body volume and surface area must be positive.")
        if any(value.si_value <= 0.0 for value in self.principal_dimensions):
            raise ValueError("Principal dimensions must be positive.")
        return self


class FaceDescriptor(BaseModel):
    """One face descriptor detached from a live solver object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stable_key: str = Field(min_length=1)
    internal_runtime_id: str = Field(min_length=1)
    parent_body_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    surface_type: SurfaceType
    area: Area
    centroid: Vector3 | None
    normal: Vector3 | None = None
    axis: Vector3 | None = None
    external: bool
    interface: bool
    adjacent_body_keys: tuple[str, ...] = ()
    bounding_box: BoundingBox
    named_selections: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_orientation(self) -> Self:
        """Require a normal or axis as stated by the geometry contract."""

        if self.normal is None and self.axis is None:
            raise ValueError("Face requires either normal or axis orientation evidence.")
        if self.area.si_value <= 0.0:
            raise ValueError("Face area must be positive.")
        if self.normal is not None and vector_norm(self.normal) <= _FRAME_TOLERANCE:
            raise ValueError("Face normal must have nonzero length.")
        if self.axis is not None and vector_norm(self.axis) <= _FRAME_TOLERANCE:
            raise ValueError("Face axis must have nonzero length.")
        return self


class GeometryGraph(BaseModel):
    """Complete deterministic geometry inspection graph for one source model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2] = 1
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    length_unit: Literal["m"] = "m"
    capability_tier: GeometryCapabilityTier = GeometryCapabilityTier.FULL_SEMANTIC
    bodies: tuple[BodyDescriptor, ...]
    faces: tuple[FaceDescriptor, ...]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        """Require unique identifiers and valid body references."""

        body_keys = [body.stable_key for body in self.bodies]
        face_keys = [face.stable_key for face in self.faces]
        runtime_ids = [body.internal_runtime_id for body in self.bodies] + [
            face.internal_runtime_id for face in self.faces
        ]
        if len(body_keys) != len(set(body_keys)):
            raise ValueError("Body stable keys must be unique.")
        if len(face_keys) != len(set(face_keys)):
            raise ValueError("Face stable keys must be unique.")
        if len(runtime_ids) != len(set(runtime_ids)):
            raise ValueError("Runtime IDs must be unique within a geometry graph.")
        known_bodies = set(body_keys)
        for face in self.faces:
            if face.parent_body_key not in known_bodies:
                raise ValueError(f"Unknown parent body key {face.parent_body_key!r}.")
            if not set(face.adjacent_body_keys).issubset(known_bodies):
                raise ValueError(f"Face {face.stable_key!r} has an unknown adjacent body.")
        return self

    def fingerprint(self) -> str:
        """Hash normalized topology evidence without run-local IDs or enumeration order."""

        def quantity(value: PhysicalQuantity) -> tuple[float, str]:
            return value.si_value, value.si_unit

        bodies = [
            {
                "key": body.stable_key,
                "name": body.display_name,
                "entity_type": body.entity_type.value,
                "solid": body.solid,
                "volume": quantity(body.volume),
                "surface_area": quantity(body.surface_area),
                "centroid": None if body.centroid is None else body.centroid.root,
                "bounds": (body.bounding_box.minimum.root, body.bounding_box.maximum.root),
                "principal_dimensions": [quantity(value) for value in body.principal_dimensions],
                "principal_axes": (
                    None
                    if body.principal_axes is None
                    else [axis.root for axis in body.principal_axes]
                ),
                "named_selections": sorted(body.named_selections),
            }
            for body in sorted(self.bodies, key=lambda item: item.stable_key)
        ]
        faces = [
            {
                "key": face.stable_key,
                "parent": face.parent_body_key,
                "name": face.display_name,
                "surface_type": face.surface_type.value,
                "area": quantity(face.area),
                "centroid": None if face.centroid is None else face.centroid.root,
                "normal": None if face.normal is None else face.normal.root,
                "axis": None if face.axis is None else face.axis.root,
                "external": face.external,
                "interface": face.interface,
                "adjacent": sorted(face.adjacent_body_keys),
                "bounds": (face.bounding_box.minimum.root, face.bounding_box.maximum.root),
                "named_selections": sorted(face.named_selections),
            }
            for face in sorted(self.faces, key=lambda item: item.stable_key)
        ]
        serialized = json.dumps(
            {
                "capability_tier": self.capability_tier.value,
                "bodies": bodies,
                "faces": faces,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(serialized.encode()).hexdigest()


def resolve_coordinate_frame(frame: CoordinateFrame, graph: GeometryGraph) -> CoordinateFrame:
    """Resolve an optional principal frame or return an already usable frame."""

    if frame.type is not CoordinateFrameType.PRINCIPAL:
        return frame
    if not graph.bodies:
        raise DomainError(
            ErrorCode.AMBIGUOUS_COORDINATE_FRAME,
            "coordinate_frame",
            "Principal frame cannot be resolved without a body.",
        )
    largest = max(graph.bodies, key=lambda body: body.volume.si_value)
    if largest.centroid is None:
        raise DomainError(
            ErrorCode.AMBIGUOUS_COORDINATE_FRAME,
            "coordinate_frame",
            "Principal frame requires an exact body centroid.",
        )
    dimensions = sorted((value.si_value for value in largest.principal_dimensions), reverse=True)
    scale = max(dimensions[0], 1.0)
    if any(
        abs(left - right) <= _FRAME_TOLERANCE * scale
        for left, right in zip(dimensions, dimensions[1:], strict=False)
    ):
        raise DomainError(
            ErrorCode.AMBIGUOUS_COORDINATE_FRAME,
            "coordinate_frame",
            "Principal dimensions are degenerate; an explicit frame is required.",
            details={"principal_dimensions_m": dimensions},
        )
    if largest.principal_axes is None:
        raise DomainError(
            ErrorCode.AMBIGUOUS_COORDINATE_FRAME,
            "coordinate_frame",
            "Geometry adapter did not provide principal-axis orientation evidence.",
        )
    x_axis, y_axis, z_axis = largest.principal_axes
    return CoordinateFrame(
        type=CoordinateFrameType.EXPLICIT,
        origin=largest.centroid,
        x_axis=x_axis,
        y_axis=y_axis,
        z_axis=z_axis,
    )
