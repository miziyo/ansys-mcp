"""Contract tests for the fail-safe G3 official Geometry adapter boundary."""

from pathlib import Path

import pytest

from ansys_research_runner.adapters.geometry.base import GeometryInspectionRequest
from ansys_research_runner.adapters.geometry.pyansys_geometry import PyAnsysGeometryAdapter
from ansys_research_runner.domain.capabilities import CapabilityStatus, GeometryBackendCapability
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import GeometryCapabilityTier


def _blocked_capability() -> GeometryBackendCapability:
    return GeometryBackendCapability(
        backend="pyansys_geometry_discovery",
        status=CapabilityStatus.BLOCKED,
        package_version="0.17.1",
        backend_version="26.1.0",
        capabilities=("body_count", "face_count", "volume"),
        missing_capabilities=("centroid", "face_orientation", "geometry_graph"),
        reason="MINIMUM_GEOMETRY_CONTRACT_UNSATISFIED",
    )


def test_adapter_reports_partial_evidence_as_unavailable() -> None:
    adapter = PyAnsysGeometryAdapter(_blocked_capability())

    report = adapter.probe_capabilities()

    assert report.available is False
    assert report.backend_version == "26.1.0"
    assert report.capabilities == ("body_count", "face_count", "volume")
    assert "centroid" in report.missing_capabilities


def test_adapter_refuses_to_fabricate_geometry_graph() -> None:
    adapter = PyAnsysGeometryAdapter(_blocked_capability())

    with pytest.raises(DomainError) as caught:
        adapter.inspect(GeometryInspectionRequest(model_path=Path("unread.step")))

    assert caught.value.code is ErrorCode.GEOMETRY_CAPABILITY_MISSING
    assert caught.value.details["missing_capabilities"] == [
        "centroid",
        "face_orientation",
        "geometry_graph",
    ]


def test_closed_adapter_never_reports_ready() -> None:
    adapter = PyAnsysGeometryAdapter(_blocked_capability())
    adapter.close()

    report = adapter.probe_capabilities()

    assert report.available is False
    assert report.reason == "ADAPTER_CLOSED"


def test_source_bound_mapping_uses_exact_source_identity_without_fake_centroid() -> None:
    source = Path("reviewed-box.scdocx").resolve()
    source_hash = "a" * 64
    raw_body = {
        "runtime_id": "ephemeral-body-1",
        "display_name": "ThermalDomain",
        "volume_m3": 6.0,
        "surface_area_m2": 22.0,
        "centroid_m": None,
        "bounding_box_min_m": [0.0, 0.0, 0.0],
        "bounding_box_max_m": [1.0, 2.0, 3.0],
        "named_selections": ["THERMAL_DOMAIN"],
    }
    body, body_key = PyAnsysGeometryAdapter._body_descriptor(raw_body, source, source_hash)
    raw_faces = [
        {
            "runtime_id": "ephemeral-face-1",
            "surface_type": "PLANE",
            "area_m2": 6.0,
            "centroid_m": None,
            "bounding_box_min_m": [0.0, 0.0, 0.0],
            "bounding_box_max_m": [0.0, 2.0, 3.0],
            "normal": [-1.0, 0.0, 0.0],
            "axis": None,
            "named_selections": ["COLD_FACE", "EXTERIOR"],
        }
    ]

    faces = PyAnsysGeometryAdapter._face_descriptors(raw_faces, body_key, source_hash)
    repeated_faces = PyAnsysGeometryAdapter._face_descriptors(
        [{**raw_faces[0], "runtime_id": "different-runtime-id"}], body_key, source_hash
    )

    assert body.centroid is None
    assert body.named_selections == ("THERMAL_DOMAIN",)
    assert faces[0].centroid is None
    assert faces[0].named_selections == ("COLD_FACE", "EXTERIOR")
    assert faces[0].stable_key == repeated_faces[0].stable_key
    assert GeometryCapabilityTier.SOURCE_BOUND_EXACT.value == "source_bound_exact"


def test_source_change_invalidates_source_bound_stable_keys() -> None:
    source = Path("reviewed-box.step").resolve()
    raw_body = {
        "runtime_id": "body",
        "display_name": "solid",
        "volume_m3": 1.0,
        "surface_area_m2": 6.0,
        "centroid_m": None,
        "bounding_box_min_m": [0.0, 0.0, 0.0],
        "bounding_box_max_m": [1.0, 1.0, 1.0],
        "named_selections": [],
    }

    first, _ = PyAnsysGeometryAdapter._body_descriptor(raw_body, source, "1" * 64)
    changed, _ = PyAnsysGeometryAdapter._body_descriptor(raw_body, source, "2" * 64)

    assert first.stable_key != changed.stable_key
