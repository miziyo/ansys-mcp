"""Deterministic, solver-free GeometryAdapter used by G2 tests."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path

from ansys_research_runner.adapters.geometry.base import (
    ArtifactRecord,
    GeometryCapabilityReport,
    GeometryInspectionRequest,
    TestGeometryKind,
    TestGeometrySpec,
)
from ansys_research_runner.domain.geometry import (
    BodyDescriptor,
    BoundingBox,
    CoordinateFrame,
    CoordinateFrameType,
    FaceDescriptor,
    GeometryGraph,
    SurfaceType,
    Vector3,
    vector_cross,
    vector_from_iterable,
)
from ansys_research_runner.domain.selectors import RegionResolution
from ansys_research_runner.domain.units import (
    PhysicalDimension,
    PhysicalQuantity,
    quantity_from_si,
)
from ansys_research_runner.io import atomic_write_json

type Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
IDENTITY_MATRIX: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
ZERO_VECTOR = Vector3((0.0, 0.0, 0.0))


def _sha256(value: object) -> str:
    serialized = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _required_centroid(value: Vector3 | None) -> Vector3:
    if value is None:
        raise ValueError("Synthetic graph transforms require exact entity centroids.")
    return value


def _bounds(center: Vector3, dimensions: tuple[float, float, float]) -> BoundingBox:
    half = tuple(value / 2.0 for value in dimensions)
    return BoundingBox(
        minimum=vector_from_iterable(center.root[index] - half[index] for index in range(3)),
        maximum=vector_from_iterable(center.root[index] + half[index] for index in range(3)),
    )


def _length_triplet(
    values: tuple[float, float, float],
) -> tuple[PhysicalQuantity, PhysicalQuantity, PhysicalQuantity]:
    ordered = sorted(values, reverse=True)
    return (
        quantity_from_si(ordered[0], PhysicalDimension.LENGTH),
        quantity_from_si(ordered[1], PhysicalDimension.LENGTH),
        quantity_from_si(ordered[2], PhysicalDimension.LENGTH),
    )


def _principal_box(
    dimensions: tuple[float, float, float],
) -> tuple[
    tuple[PhysicalQuantity, PhysicalQuantity, PhysicalQuantity],
    tuple[Vector3, Vector3, Vector3],
]:
    axes = (
        Vector3((1.0, 0.0, 0.0)),
        Vector3((0.0, 1.0, 0.0)),
        Vector3((0.0, 0.0, 1.0)),
    )
    ordered = sorted(zip(dimensions, axes, strict=True), key=lambda item: item[0], reverse=True)
    lengths = (
        quantity_from_si(ordered[0][0], PhysicalDimension.LENGTH),
        quantity_from_si(ordered[1][0], PhysicalDimension.LENGTH),
        quantity_from_si(ordered[2][0], PhysicalDimension.LENGTH),
    )
    principal_axes = (
        ordered[0][1],
        ordered[1][1],
        vector_cross(ordered[0][1], ordered[1][1]),
    )
    return lengths, principal_axes


def _box_components(
    prefix: str,
    center: Vector3,
    dimensions: tuple[float, float, float],
    source_path: str,
) -> tuple[BodyDescriptor, tuple[FaceDescriptor, ...]]:
    dx, dy, dz = dimensions
    bounds = _bounds(center, dimensions)
    principal_dimensions, principal_axes = _principal_box(dimensions)
    body = BodyDescriptor(
        stable_key=f"{prefix}.body",
        internal_runtime_id=f"runtime:{prefix}:body",
        display_name=f"{prefix}_body",
        volume=quantity_from_si(dx * dy * dz, PhysicalDimension.VOLUME),
        surface_area=quantity_from_si(
            2.0 * (dx * dy + dx * dz + dy * dz),
            PhysicalDimension.AREA,
        ),
        centroid=center,
        bounding_box=bounds,
        principal_dimensions=principal_dimensions,
        principal_axes=principal_axes,
        source_path=source_path,
    )
    descriptions = (
        ("x_min", 0, -1.0, dy * dz),
        ("x_max", 0, 1.0, dy * dz),
        ("y_min", 1, -1.0, dx * dz),
        ("y_max", 1, 1.0, dx * dz),
        ("z_min", 2, -1.0, dx * dy),
        ("z_max", 2, 1.0, dx * dy),
    )
    faces: list[FaceDescriptor] = []
    for name, axis_index, sign, area in descriptions:
        face_center = list(center.root)
        face_center[axis_index] += sign * dimensions[axis_index] / 2.0
        normal = [0.0, 0.0, 0.0]
        normal[axis_index] = sign
        minimum = list(bounds.minimum.root)
        maximum = list(bounds.maximum.root)
        minimum[axis_index] = face_center[axis_index]
        maximum[axis_index] = face_center[axis_index]
        faces.append(
            FaceDescriptor(
                stable_key=f"{prefix}.face.{name}",
                internal_runtime_id=f"runtime:{prefix}:face:{name}",
                parent_body_key=body.stable_key,
                display_name=f"{prefix}_{name}",
                surface_type=SurfaceType.PLANAR,
                area=quantity_from_si(area, PhysicalDimension.AREA),
                centroid=vector_from_iterable(face_center),
                normal=vector_from_iterable(normal),
                external=True,
                interface=False,
                bounding_box=BoundingBox(
                    minimum=vector_from_iterable(minimum),
                    maximum=vector_from_iterable(maximum),
                ),
            )
        )
    return body, tuple(faces)


def _cylinder_graph(spec: TestGeometrySpec) -> GeometryGraph:
    radius = spec.radius_m
    length = spec.length_m
    source_path = "synthetic://cylinder"
    body_key = "cylinder.body"
    body = BodyDescriptor(
        stable_key=body_key,
        internal_runtime_id="runtime:cylinder:body",
        display_name="cylinder_body",
        volume=quantity_from_si(math.pi * radius * radius * length, PhysicalDimension.VOLUME),
        surface_area=quantity_from_si(
            2.0 * math.pi * radius * (length + radius),
            PhysicalDimension.AREA,
        ),
        centroid=Vector3((0.0, 0.0, 0.0)),
        bounding_box=BoundingBox(
            minimum=Vector3((-length / 2.0, -radius, -radius)),
            maximum=Vector3((length / 2.0, radius, radius)),
        ),
        principal_dimensions=_length_triplet((length, 2.0 * radius, 2.0 * radius)),
        principal_axes=(
            Vector3((1.0, 0.0, 0.0)),
            Vector3((0.0, 1.0, 0.0)),
            Vector3((0.0, 0.0, 1.0)),
        ),
        source_path=source_path,
    )
    end_area = math.pi * radius * radius
    faces = (
        FaceDescriptor(
            stable_key="cylinder.face.x_min",
            internal_runtime_id="runtime:cylinder:face:x_min",
            parent_body_key=body_key,
            display_name="cylinder_x_min",
            surface_type=SurfaceType.PLANAR,
            area=quantity_from_si(end_area, PhysicalDimension.AREA),
            centroid=Vector3((-length / 2.0, 0.0, 0.0)),
            normal=Vector3((-1.0, 0.0, 0.0)),
            external=True,
            interface=False,
            bounding_box=BoundingBox(
                minimum=Vector3((-length / 2.0, -radius, -radius)),
                maximum=Vector3((-length / 2.0, radius, radius)),
            ),
        ),
        FaceDescriptor(
            stable_key="cylinder.face.x_max",
            internal_runtime_id="runtime:cylinder:face:x_max",
            parent_body_key=body_key,
            display_name="cylinder_x_max",
            surface_type=SurfaceType.PLANAR,
            area=quantity_from_si(end_area, PhysicalDimension.AREA),
            centroid=Vector3((length / 2.0, 0.0, 0.0)),
            normal=Vector3((1.0, 0.0, 0.0)),
            external=True,
            interface=False,
            bounding_box=BoundingBox(
                minimum=Vector3((length / 2.0, -radius, -radius)),
                maximum=Vector3((length / 2.0, radius, radius)),
            ),
        ),
        FaceDescriptor(
            stable_key="cylinder.face.side",
            internal_runtime_id="runtime:cylinder:face:side",
            parent_body_key=body_key,
            display_name="cylinder_side",
            surface_type=SurfaceType.CYLINDRICAL,
            area=quantity_from_si(2.0 * math.pi * radius * length, PhysicalDimension.AREA),
            centroid=Vector3((0.0, 0.0, 0.0)),
            axis=Vector3((1.0, 0.0, 0.0)),
            external=True,
            interface=False,
            bounding_box=body.bounding_box,
        ),
    )
    return GeometryGraph(
        source_path=source_path,
        source_sha256=_sha256(spec.model_dump(mode="json")),
        bodies=(body,),
        faces=faces,
    )


def synthetic_graph(spec: TestGeometrySpec) -> GeometryGraph:
    """Build one required synthetic geometry graph."""

    if spec.kind is TestGeometryKind.CYLINDER:
        return _cylinder_graph(spec)
    source_path = f"synthetic://{spec.kind.value}"
    if spec.kind is TestGeometryKind.BOX:
        body, faces = _box_components(
            "box", Vector3((0.0, 0.0, 0.0)), spec.dimensions_m, source_path
        )
        bodies: tuple[BodyDescriptor, ...] = (body,)
    elif spec.kind is TestGeometryKind.MULTI_BODY_BOX:
        left = _box_components("small", Vector3((-2.0, 0.0, 0.0)), (1.0, 1.0, 1.0), source_path)
        right = _box_components("large", Vector3((2.0, 0.0, 0.0)), (2.0, 2.0, 2.0), source_path)
        bodies = (left[0], right[0])
        faces = left[1] + right[1]
    else:
        lower = _box_components(
            "symmetric_a", Vector3((0.0, -2.0, 0.0)), spec.dimensions_m, source_path
        )
        upper = _box_components(
            "symmetric_b", Vector3((0.0, 2.0, 0.0)), spec.dimensions_m, source_path
        )
        bodies = (lower[0], upper[0])
        faces = lower[1] + upper[1]
    return GeometryGraph(
        source_path=source_path,
        source_sha256=_sha256(spec.model_dump(mode="json")),
        bodies=bodies,
        faces=faces,
    )


def _matrix_vector(matrix: Matrix3, vector: Vector3) -> Vector3:
    return vector_from_iterable(
        sum(matrix[row][column] * vector.root[column] for column in range(3)) for row in range(3)
    )


def _transform_axes(
    matrix: Matrix3,
    axes: tuple[Vector3, Vector3, Vector3] | None,
) -> tuple[Vector3, Vector3, Vector3] | None:
    if axes is None:
        return None
    return (
        _matrix_vector(matrix, axes[0]),
        _matrix_vector(matrix, axes[1]),
        _matrix_vector(matrix, axes[2]),
    )


def _transform_point(
    point: Vector3,
    matrix: Matrix3,
    translation: Vector3,
    scale: float,
) -> Vector3:
    rotated = _matrix_vector(matrix, vector_from_iterable(value * scale for value in point.root))
    return vector_from_iterable(rotated.root[index] + translation.root[index] for index in range(3))


def _transform_bounds(
    bounds: BoundingBox,
    matrix: Matrix3,
    translation: Vector3,
    scale: float,
) -> BoundingBox:
    corners = [
        _transform_point(vector_from_iterable(values), matrix, translation, scale)
        for values in itertools.product(
            (bounds.minimum.x, bounds.maximum.x),
            (bounds.minimum.y, bounds.maximum.y),
            (bounds.minimum.z, bounds.maximum.z),
        )
    ]
    return BoundingBox(
        minimum=vector_from_iterable(
            min(point.root[index] for point in corners) for index in range(3)
        ),
        maximum=vector_from_iterable(
            max(point.root[index] for point in corners) for index in range(3)
        ),
    )


def transform_graph(
    graph: GeometryGraph,
    *,
    matrix: Matrix3 = IDENTITY_MATRIX,
    translation: Vector3 = ZERO_VECTOR,
    scale: float = 1.0,
    runtime_prefix: str = "transformed",
    reverse_order: bool = False,
) -> GeometryGraph:
    """Apply a rigid transform and uniform scale while remapping run-local IDs."""

    if scale <= 0.0:
        raise ValueError("Uniform scale must be positive.")
    if any(body.centroid is None for body in graph.bodies) or any(
        face.centroid is None for face in graph.faces
    ):
        raise ValueError("Synthetic graph transforms require exact entity centroids.")
    bodies = tuple(
        BodyDescriptor(
            stable_key=body.stable_key,
            internal_runtime_id=f"{runtime_prefix}:body:{index}",
            display_name=body.display_name,
            entity_type=body.entity_type,
            solid=body.solid,
            volume=quantity_from_si(body.volume.si_value * scale**3, PhysicalDimension.VOLUME),
            surface_area=quantity_from_si(
                body.surface_area.si_value * scale**2,
                PhysicalDimension.AREA,
            ),
            centroid=_transform_point(
                _required_centroid(body.centroid),
                matrix,
                translation,
                scale,
            ),
            bounding_box=_transform_bounds(body.bounding_box, matrix, translation, scale),
            principal_dimensions=(
                quantity_from_si(
                    body.principal_dimensions[0].si_value * scale,
                    PhysicalDimension.LENGTH,
                ),
                quantity_from_si(
                    body.principal_dimensions[1].si_value * scale,
                    PhysicalDimension.LENGTH,
                ),
                quantity_from_si(
                    body.principal_dimensions[2].si_value * scale,
                    PhysicalDimension.LENGTH,
                ),
            ),
            principal_axes=_transform_axes(matrix, body.principal_axes),
            named_selections=body.named_selections,
            source_path=body.source_path,
            metadata=body.metadata,
        )
        for index, body in enumerate(graph.bodies)
    )
    faces = tuple(
        FaceDescriptor(
            stable_key=face.stable_key,
            internal_runtime_id=f"{runtime_prefix}:face:{index}",
            parent_body_key=face.parent_body_key,
            display_name=face.display_name,
            surface_type=face.surface_type,
            area=quantity_from_si(face.area.si_value * scale**2, PhysicalDimension.AREA),
            centroid=_transform_point(
                _required_centroid(face.centroid),
                matrix,
                translation,
                scale,
            ),
            normal=None if face.normal is None else _matrix_vector(matrix, face.normal),
            axis=None if face.axis is None else _matrix_vector(matrix, face.axis),
            external=face.external,
            interface=face.interface,
            adjacent_body_keys=face.adjacent_body_keys,
            bounding_box=_transform_bounds(face.bounding_box, matrix, translation, scale),
            named_selections=face.named_selections,
            metadata=face.metadata,
        )
        for index, face in enumerate(graph.faces)
    )
    if reverse_order:
        bodies = tuple(reversed(bodies))
        faces = tuple(reversed(faces))
    transform_identity = {
        "source": graph.source_sha256,
        "matrix": matrix,
        "translation": translation.root,
        "scale": scale,
    }
    return GeometryGraph(
        schema_version=graph.schema_version,
        source_path=graph.source_path,
        source_sha256=_sha256(transform_identity),
        length_unit=graph.length_unit,
        capability_tier=graph.capability_tier,
        bodies=bodies,
        faces=faces,
        metadata=graph.metadata,
    )


def transform_frame(
    frame: CoordinateFrame,
    *,
    matrix: Matrix3,
    translation: Vector3,
    scale: float = 1.0,
) -> CoordinateFrame:
    """Apply the same model transform to an explicit coordinate frame."""

    if frame.type is not CoordinateFrameType.EXPLICIT:
        raise ValueError("Only an explicit frame can accompany a rotation invariance test.")
    assert frame.x_axis is not None
    assert frame.y_axis is not None
    return CoordinateFrame(
        type=CoordinateFrameType.EXPLICIT,
        origin=_transform_point(frame.origin, matrix, translation, scale),
        x_axis=_matrix_vector(matrix, frame.x_axis),
        y_axis=_matrix_vector(matrix, frame.y_axis),
        z_axis=None if frame.z_axis is None else _matrix_vector(matrix, frame.z_axis),
    )


def split_planar_face(graph: GeometryGraph, stable_key: str) -> GeometryGraph:
    """Replace one planar face by two equal-area synthetic topology fragments."""

    original = next((face for face in graph.faces if face.stable_key == stable_key), None)
    if original is None:
        raise KeyError(stable_key)
    if original.surface_type is not SurfaceType.PLANAR or original.normal is None:
        raise ValueError("Only a planar face with a normal can be split.")
    if original.centroid is None:
        raise ValueError("Synthetic split requires an exact face centroid.")
    spans = [
        original.bounding_box.maximum.root[index] - original.bounding_box.minimum.root[index]
        for index in range(3)
    ]
    split_axis = max(range(3), key=lambda index: spans[index])
    if spans[split_axis] <= 0.0:
        raise ValueError("Planar face has no splittable bounding-box span.")
    midpoint = original.centroid.root[split_axis]
    fragments: list[FaceDescriptor] = []
    for index, (low, high) in enumerate(
        (
            (original.bounding_box.minimum.root[split_axis], midpoint),
            (midpoint, original.bounding_box.maximum.root[split_axis]),
        )
    ):
        minimum = list(original.bounding_box.minimum.root)
        maximum = list(original.bounding_box.maximum.root)
        minimum[split_axis] = low
        maximum[split_axis] = high
        centroid = list(original.centroid.root)
        centroid[split_axis] = (low + high) / 2.0
        fragments.append(
            FaceDescriptor(
                stable_key=f"{original.stable_key}.split_{index}",
                internal_runtime_id=f"split:face:{index}",
                parent_body_key=original.parent_body_key,
                display_name=f"{original.display_name}_split_{index}",
                surface_type=original.surface_type,
                area=quantity_from_si(original.area.si_value / 2.0, PhysicalDimension.AREA),
                centroid=vector_from_iterable(centroid),
                normal=original.normal,
                axis=original.axis,
                external=original.external,
                interface=original.interface,
                adjacent_body_keys=original.adjacent_body_keys,
                bounding_box=BoundingBox(
                    minimum=vector_from_iterable(minimum),
                    maximum=vector_from_iterable(maximum),
                ),
                named_selections=original.named_selections,
                metadata={**original.metadata, "synthetic_split_parent": stable_key},
            )
        )
    faces = tuple(face for face in graph.faces if face.stable_key != stable_key) + tuple(fragments)
    return GeometryGraph(
        schema_version=graph.schema_version,
        source_path=graph.source_path,
        source_sha256=_sha256({"source": graph.source_sha256, "split": stable_key}),
        length_unit=graph.length_unit,
        capability_tier=graph.capability_tier,
        bodies=graph.bodies,
        faces=faces,
        metadata=graph.metadata,
    )


class SyntheticGeometryAdapter:
    """Functional local adapter that persists and inspects generated fixture specs."""

    def __init__(self) -> None:
        self._closed = False

    def probe_capabilities(self) -> GeometryCapabilityReport:
        """Report the complete deterministic synthetic capability set."""

        return GeometryCapabilityReport(
            backend="synthetic",
            available=not self._closed,
            capabilities=(
                "box",
                "cylinder",
                "multi_body_box",
                "ambiguous_symmetric",
                "transform",
                "fingerprint",
            ),
            reason="ADAPTER_CLOSED" if self._closed else None,
        )

    def inspect(self, request: GeometryInspectionRequest) -> GeometryGraph:
        """Load a generated fixture specification and return its Geometry Graph."""

        if self._closed:
            raise RuntimeError("Synthetic geometry adapter is closed.")
        payload = json.loads(request.model_path.read_text(encoding="utf-8"))
        return synthetic_graph(TestGeometrySpec.model_validate(payload))

    def generate_test_asset(self, spec: TestGeometrySpec, output_dir: Path) -> Path:
        """Persist a portable, non-proprietary synthetic geometry specification."""

        if self._closed:
            raise RuntimeError("Synthetic geometry adapter is closed.")
        path = output_dir / f"{spec.kind.value}.synthetic.json"
        atomic_write_json(path, spec.model_dump(mode="json"))
        return path

    def create_selection_preview(
        self,
        graph: GeometryGraph,
        resolution: RegionResolution,
        output_dir: Path,
    ) -> list[ArtifactRecord]:
        """Persist a compact JSON selection preview with no rendered proprietary data."""

        payload = {
            "geometry_fingerprint": graph.fingerprint(),
            "resolution": resolution.model_dump(mode="json", by_alias=True),
        }
        path = output_dir / "selection-preview.json"
        atomic_write_json(path, payload)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return [ArtifactRecord(path=path, media_type="application/json", sha256=digest)]

    def close(self) -> None:
        """Mark the local adapter closed; it owns no external process."""

        self._closed = True
