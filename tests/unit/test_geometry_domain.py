"""G2 geometry graph, frame, and synthetic adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ansys_research_runner.adapters.geometry.base import (
    GeometryAdapter,
    GeometryInspectionRequest,
)
from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import (
    TestGeometrySpec as GeometrySpec,
)
from ansys_research_runner.adapters.geometry.synthetic import (
    SyntheticGeometryAdapter,
    synthetic_graph,
    transform_graph,
)
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import (
    BoundingBox,
    CoordinateFrame,
    CoordinateFrameType,
    FaceDescriptor,
    GeometryGraph,
    SurfaceType,
    Vector3,
    resolve_coordinate_frame,
    vector_cross,
    vector_dot,
    vector_norm,
    vector_normalize,
)
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.domain.units import PhysicalDimension, quantity_from_si
from tests.g2_fixtures import box_graph, box_manifest


def _accepts_protocol(adapter: GeometryAdapter) -> GeometryAdapter:
    return adapter


def test_vector_math_and_bounding_box() -> None:
    x = Vector3((1.0, 0.0, 0.0))
    y = Vector3((0.0, 1.0, 0.0))
    assert vector_dot(x, y) == 0.0
    assert vector_cross(x, y) == Vector3((0.0, 0.0, 1.0))
    assert vector_norm(Vector3((3.0, 4.0, 0.0))) == 5.0
    assert vector_normalize(Vector3((2.0, 0.0, 0.0))) == x
    bounds = BoundingBox(minimum=[0, 0, 0], maximum=[1, 2, 2])
    assert bounds.diagonal == 3.0


def test_invalid_vectors_and_bounds_are_rejected() -> None:
    with pytest.raises(ValidationError, match="finite"):
        Vector3((float("nan"), 0.0, 0.0))
    with pytest.raises(ValidationError, match="minimum"):
        BoundingBox(minimum=[1, 0, 0], maximum=[0, 1, 1])
    with pytest.raises(DomainError) as failure:
        vector_normalize(Vector3((0.0, 0.0, 0.0)))
    assert failure.value.code is ErrorCode.INVALID_COORDINATE_FRAME


def test_coordinate_frames_validate_and_transform() -> None:
    global_frame = CoordinateFrame()
    assert global_frame.axes()[0] == Vector3((1.0, 0.0, 0.0))
    frame = CoordinateFrame.model_validate(
        {
            "type": "explicit",
            "origin": [10, 0, 0],
            "x_axis": [0, 1, 0],
            "y_axis": [-1, 0, 0],
        }
    )
    assert frame.to_local_point(Vector3((10.0, 2.0, 0.0))).root == pytest.approx((2.0, 0.0, 0.0))
    assert frame.to_local_direction(Vector3((0.0, 1.0, 0.0))).root == (1.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "cad_global", "x_axis": [1, 0, 0]},
        {"type": "explicit", "x_axis": [1, 0, 0]},
        {"type": "explicit", "x_axis": [1, 0, 0], "y_axis": [1, 0, 0]},
        {
            "type": "explicit",
            "x_axis": [1, 0, 0],
            "y_axis": [0, 1, 0],
            "z_axis": [0, 0, -1],
        },
        {"type": "principal", "x_axis": [1, 0, 0]},
    ],
)
def test_invalid_coordinate_frames_fail(payload: dict[str, object]) -> None:
    with pytest.raises((ValidationError, DomainError)):
        CoordinateFrame.model_validate(payload)


def test_principal_frame_is_optional_and_fails_on_degeneracy() -> None:
    resolved = resolve_coordinate_frame(
        CoordinateFrame(type=CoordinateFrameType.PRINCIPAL), box_graph()
    )
    assert resolved.type is CoordinateFrameType.EXPLICIT
    assert resolved.x_axis == Vector3((0.0, 0.0, 1.0))
    cube = synthetic_graph(GeometrySpec(kind=GeometryKind.BOX, dimensions_m=(1.0, 1.0, 1.0)))
    with pytest.raises(DomainError) as failure:
        resolve_coordinate_frame(CoordinateFrame(type="principal"), cube)
    assert failure.value.code is ErrorCode.AMBIGUOUS_COORDINATE_FRAME


def test_principal_frame_requires_adapter_orientation_evidence() -> None:
    graph = box_graph()
    body = graph.bodies[0].model_copy(update={"principal_axes": None})
    graph_without_axes = graph.model_copy(update={"bodies": (body,)})
    with pytest.raises(DomainError) as failure:
        resolve_coordinate_frame(
            CoordinateFrame(type=CoordinateFrameType.PRINCIPAL), graph_without_axes
        )
    assert failure.value.code is ErrorCode.AMBIGUOUS_COORDINATE_FRAME
    assert "did not provide" in failure.value.message


def test_geometry_graph_validates_runtime_and_parent_ids() -> None:
    graph = box_graph()
    duplicate = graph.model_dump(mode="python")
    duplicate["faces"][1]["internal_runtime_id"] = duplicate["faces"][0]["internal_runtime_id"]
    with pytest.raises(ValidationError, match="Runtime IDs"):
        GeometryGraph.model_validate(duplicate)
    unknown_parent = graph.model_dump(mode="python")
    unknown_parent["faces"][0]["parent_body_key"] = "missing"
    with pytest.raises(ValidationError, match="Unknown parent"):
        GeometryGraph.model_validate(unknown_parent)


def test_face_requires_orientation() -> None:
    with pytest.raises(ValidationError, match="normal or axis"):
        FaceDescriptor(
            stable_key="f",
            internal_runtime_id="runtime:f",
            parent_body_key="b",
            display_name="f",
            surface_type=SurfaceType.UNKNOWN,
            area=quantity_from_si(1.0, PhysicalDimension.AREA),
            centroid=Vector3((0.0, 0.0, 0.0)),
            external=True,
            interface=False,
            bounding_box=BoundingBox(minimum=[0, 0, 0], maximum=[0, 0, 0]),
        )


def test_geometry_measures_and_orientation_must_be_physical() -> None:
    graph = box_graph()
    body_payload = graph.bodies[0].model_dump(mode="python")
    body_payload["volume"] = "-1 m^3"
    with pytest.raises(ValidationError, match="must be positive"):
        type(graph.bodies[0]).model_validate(body_payload)
    face_payload = graph.faces[0].model_dump(mode="python")
    face_payload["normal"] = [0.0, 0.0, 0.0]
    with pytest.raises(ValidationError, match="nonzero"):
        FaceDescriptor.model_validate(face_payload)


def test_fingerprint_ignores_order_and_runtime_ids() -> None:
    graph = box_graph()
    transformed = transform_graph(graph, runtime_prefix="new-run", reverse_order=True)
    assert transformed.fingerprint() == graph.fingerprint()
    translated = transform_graph(graph, translation=Vector3((5.0, -2.0, 1.0)))
    assert translated.fingerprint() != graph.fingerprint()


@pytest.mark.parametrize(
    ("kind", "body_count", "face_count"),
    [
        (GeometryKind.BOX, 1, 6),
        (GeometryKind.CYLINDER, 1, 3),
        (GeometryKind.MULTI_BODY_BOX, 2, 12),
        (GeometryKind.AMBIGUOUS_SYMMETRIC, 2, 12),
    ],
)
def test_required_synthetic_fixture_counts(
    kind: GeometryKind, body_count: int, face_count: int
) -> None:
    graph = synthetic_graph(GeometrySpec(kind=kind))
    assert len(graph.bodies) == body_count
    assert len(graph.faces) == face_count
    assert len(graph.fingerprint()) == 64


def test_synthetic_adapter_contract_and_artifacts(tmp_path: Path) -> None:
    adapter = _accepts_protocol(SyntheticGeometryAdapter())
    assert adapter.probe_capabilities().available
    asset = adapter.generate_test_asset(GeometrySpec(kind="box"), tmp_path)
    graph = adapter.inspect(GeometryInspectionRequest(model_path=asset))
    resolution = resolve_regions(graph, box_manifest().roles, box_manifest().coordinate_frame)
    records = adapter.create_selection_preview(graph, resolution, tmp_path)
    assert records[0].path.is_file()
    assert (
        json.loads(records[0].path.read_text(encoding="utf-8"))["geometry_fingerprint"]
        == graph.fingerprint()
    )
    adapter.close()
    assert not adapter.probe_capabilities().available
    with pytest.raises(RuntimeError, match="closed"):
        adapter.inspect(GeometryInspectionRequest(model_path=asset))
