"""Temperature-field HDF5 schema and data-integrity tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ansys_research_runner.services.field_service import (
    TemperatureFieldData,
    mesh_sha256,
    validate_temperature_field,
    write_temperature_field,
)


def _field(*, duplicate_node: bool = False) -> TemperatureFieldData:
    node_ids = np.array([7, 3, 7 if duplicate_node else 9], dtype=np.int64)
    coordinates = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=np.float64)
    elements = np.array([42], dtype=np.int64)
    connectivity = np.array([[7, 3, 9]], dtype=np.int64)
    return TemperatureFieldData(
        node_ids=node_ids,
        coordinates_m=coordinates,
        element_ids=elements,
        connectivity=connectivity,
        times_s=np.array([0.0, 1.0], dtype=np.float64),
        temperature_K=np.array([[300, 310, 320], [305, 315, 325]], dtype=np.float64),
        mesh_sha256=mesh_sha256(node_ids, coordinates, elements, connectivity),
    )


def test_field_round_trip_preserves_node_order_and_frames(tmp_path: Path) -> None:
    path = tmp_path / "temperature.h5"
    field = _field()
    written = write_temperature_field(path, field)
    checked = validate_temperature_field(path, expected_mesh_sha256=field.mesh_sha256)
    assert written.valid and checked.valid
    assert (checked.node_count, checked.element_count, checked.frame_count) == (3, 1, 2)


def test_field_writer_rejects_duplicate_node_ids_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "invalid.h5"
    report = write_temperature_field(path, _field(duplicate_node=True))
    assert not report.valid
    assert {issue.code for issue in report.issues} == {"NODE_ID_NOT_UNIQUE"}
    assert not path.exists()
