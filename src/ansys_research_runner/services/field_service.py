"""Write and validate the versioned thermal field HDF5 contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import h5py  # type: ignore[import-untyped]
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class TemperatureFieldData:
    """In-memory arrays preserving solver-provided node and frame ordering."""

    node_ids: npt.NDArray[np.int64]
    coordinates_m: npt.NDArray[np.float64]
    element_ids: npt.NDArray[np.int64]
    connectivity: npt.NDArray[np.int64] | None
    times_s: npt.NDArray[np.float64]
    temperature_K: npt.NDArray[np.float64]
    mesh_sha256: str


class FieldValidationIssue(BaseModel):
    """One deterministic HDF5 schema or data-integrity failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    path: str = Field(min_length=1)
    message: str = Field(min_length=1)


class FieldValidationReport(BaseModel):
    """Result of validating one persisted thermal field artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    valid: bool
    node_count: int = Field(ge=0)
    element_count: int = Field(ge=0)
    frame_count: int = Field(ge=0)
    time_values_s: tuple[float, ...] = ()
    mesh_sha256: str | None = None
    issues: tuple[FieldValidationIssue, ...] = ()


def mesh_sha256(
    node_ids: npt.NDArray[np.int64],
    coordinates_m: npt.NDArray[np.float64],
    element_ids: npt.NDArray[np.int64],
    connectivity: npt.NDArray[np.int64] | None,
) -> str:
    """Hash mesh arrays in their original ordering with dtype and shape evidence."""

    digest = hashlib.sha256()
    for name, array in (
        ("node_ids", node_ids),
        ("coordinates_m", coordinates_m),
        ("element_ids", element_ids),
        ("connectivity", connectivity),
    ):
        digest.update(name.encode())
        if array is None:
            digest.update(b"none")
            continue
        contiguous = np.ascontiguousarray(array)
        digest.update(str(contiguous.dtype).encode())
        digest.update(str(contiguous.shape).encode())
        digest.update(contiguous.tobytes(order="C"))
    return digest.hexdigest()


def _validate_arrays(data: TemperatureFieldData) -> tuple[FieldValidationIssue, ...]:
    issues: list[FieldValidationIssue] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append(FieldValidationIssue(code=code, path=path, message=message))

    if data.node_ids.ndim != 1:
        add("NODE_ID_SHAPE", "/mesh/node_ids", "node_ids must be one-dimensional.")
    if data.coordinates_m.ndim != 2 or data.coordinates_m.shape[1:] != (3,):
        add("COORDINATE_SHAPE", "/mesh/coordinates", "coordinates must have shape (N, 3).")
    if (
        data.coordinates_m.ndim >= 1
        and data.node_ids.ndim >= 1
        and data.coordinates_m.shape[0] != data.node_ids.shape[0]
    ):
        add("NODE_COORDINATE_MISMATCH", "/mesh", "node_ids and coordinates lengths differ.")
    if data.element_ids.ndim != 1:
        add("ELEMENT_ID_SHAPE", "/mesh/element_ids", "element_ids must be one-dimensional.")
    if (
        data.connectivity is not None
        and data.connectivity.ndim >= 1
        and data.element_ids.ndim >= 1
        and data.connectivity.shape[0] != data.element_ids.shape[0]
    ):
        add(
            "ELEMENT_CONNECTIVITY_MISMATCH",
            "/mesh/connectivity",
            "connectivity rows must match element_ids.",
        )
    if data.times_s.ndim != 1:
        add("TIME_SHAPE", "/time/values", "time values must be one-dimensional.")
    if data.times_s.size > 1 and np.any(np.diff(data.times_s) <= 0.0):
        add("TIME_NOT_STRICTLY_INCREASING", "/time/values", "time values must increase.")
    if data.temperature_K.ndim != 2:
        add(
            "TEMPERATURE_SHAPE",
            "/fields/temperature",
            "temperature must have shape (frames, nodes).",
        )
    elif data.temperature_K.shape != (data.times_s.size, data.node_ids.size):
        add(
            "FIELD_INDEX_MISMATCH",
            "/fields/temperature",
            "temperature frame/node dimensions do not match time and mesh arrays.",
        )
    if np.unique(data.node_ids).size != data.node_ids.size:
        add("NODE_ID_NOT_UNIQUE", "/mesh/node_ids", "node IDs must be unique.")
    for path, array in (
        ("/mesh/coordinates", data.coordinates_m),
        ("/time/values", data.times_s),
        ("/fields/temperature", data.temperature_K),
    ):
        if not np.all(np.isfinite(array)):
            add("NONFINITE_VALUE", path, "Array contains NaN or infinity.")
    calculated_mesh_hash = mesh_sha256(
        data.node_ids,
        data.coordinates_m,
        data.element_ids,
        data.connectivity,
    )
    if calculated_mesh_hash != data.mesh_sha256:
        add("MESH_HASH_MISMATCH", "/metadata/mesh_hash", "Mesh hash does not match arrays.")
    return tuple(issues)


def write_temperature_field(path: Path, data: TemperatureFieldData) -> FieldValidationReport:
    """Atomically write a valid HDF5 field without reordering solver arrays."""

    issues = _validate_arrays(data)
    if issues:
        return FieldValidationReport(
            valid=False,
            node_count=int(data.node_ids.size),
            element_count=int(data.element_ids.size),
            frame_count=int(data.times_s.size),
            mesh_sha256=data.mesh_sha256,
            issues=issues,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with h5py.File(temporary, "w") as output:
        metadata = output.create_group("metadata")
        metadata.create_dataset("schema_version", data=1)
        metadata.create_dataset("result_type", data="temperature", dtype=h5py.string_dtype())
        metadata.create_dataset("unit", data="K", dtype=h5py.string_dtype())
        metadata.create_dataset("mesh_hash", data=data.mesh_sha256, dtype=h5py.string_dtype())
        mesh = output.create_group("mesh")
        mesh.create_dataset("node_ids", data=data.node_ids)
        mesh.create_dataset("coordinates", data=data.coordinates_m)
        mesh.create_dataset("element_ids", data=data.element_ids)
        if data.connectivity is not None:
            mesh.create_dataset("connectivity", data=data.connectivity)
        time_group = output.create_group("time")
        time_group.create_dataset("values", data=data.times_s)
        fields = output.create_group("fields")
        fields.create_dataset("temperature", data=data.temperature_K)
        output.flush()
    temporary.replace(path)
    return validate_temperature_field(
        path,
        expected_mesh_sha256=data.mesh_sha256,
        expected_times_s=tuple(float(value) for value in data.times_s),
    )


def _decode_scalar(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def validate_temperature_field(
    path: Path,
    *,
    expected_mesh_sha256: str | None = None,
    expected_times_s: tuple[float, ...] | None = None,
) -> FieldValidationReport:
    """Validate required HDF5 paths, indexes, finiteness, and mesh identity."""

    issues: list[FieldValidationIssue] = []

    def add(code: str, field_path: str, message: str) -> None:
        issues.append(FieldValidationIssue(code=code, path=field_path, message=message))

    required = (
        "/metadata/schema_version",
        "/metadata/result_type",
        "/metadata/unit",
        "/metadata/mesh_hash",
        "/mesh/node_ids",
        "/mesh/coordinates",
        "/mesh/element_ids",
        "/time/values",
        "/fields/temperature",
    )
    try:
        with h5py.File(path, "r") as source:
            missing = [name for name in required if name not in source]
            for name in missing:
                add("FIELD_PATH_MISSING", name, "Required HDF5 path is missing.")
            if missing:
                return FieldValidationReport(
                    valid=False,
                    node_count=0,
                    element_count=0,
                    frame_count=0,
                    issues=tuple(issues),
                )
            node_ids = np.asarray(source["/mesh/node_ids"], dtype=np.int64)
            coordinates = np.asarray(source["/mesh/coordinates"], dtype=np.float64)
            element_ids = np.asarray(source["/mesh/element_ids"], dtype=np.int64)
            connectivity = (
                np.asarray(source["/mesh/connectivity"], dtype=np.int64)
                if "/mesh/connectivity" in source
                else None
            )
            times = np.asarray(source["/time/values"], dtype=np.float64)
            temperatures = np.asarray(source["/fields/temperature"], dtype=np.float64)
            stored_hash = _decode_scalar(source["/metadata/mesh_hash"][()])
            data = TemperatureFieldData(
                node_ids=node_ids,
                coordinates_m=coordinates,
                element_ids=element_ids,
                connectivity=connectivity,
                times_s=times,
                temperature_K=temperatures,
                mesh_sha256=stored_hash,
            )
            issues.extend(_validate_arrays(data))
            if int(source["/metadata/schema_version"][()]) != 1:
                add("FIELD_SCHEMA_UNSUPPORTED", "/metadata/schema_version", "Expected version 1.")
            if _decode_scalar(source["/metadata/result_type"][()]) != "temperature":
                add("FIELD_TYPE_MISMATCH", "/metadata/result_type", "Expected temperature.")
            if _decode_scalar(source["/metadata/unit"][()]) != "K":
                add("FIELD_UNIT_MISMATCH", "/metadata/unit", "Expected kelvin.")
            if expected_mesh_sha256 is not None and stored_hash != expected_mesh_sha256:
                add(
                    "EXPECTED_MESH_HASH_MISMATCH",
                    "/metadata/mesh_hash",
                    "Stored mesh hash differs from the run manifest.",
                )
            if expected_times_s is not None:
                expected_times = np.asarray(expected_times_s, dtype=np.float64)
                if times.shape != expected_times.shape or not np.allclose(
                    times,
                    expected_times,
                    rtol=0.0,
                    atol=1.0e-12,
                ):
                    add(
                        "EXPECTED_TIME_FRAME_MISMATCH",
                        "/time/values",
                        "Stored time frames differ from the requested result frames.",
                    )
    except OSError as exc:
        add("FIELD_FILE_INVALID", str(path), str(exc))
        return FieldValidationReport(
            valid=False,
            node_count=0,
            element_count=0,
            frame_count=0,
            issues=tuple(issues),
        )
    return FieldValidationReport(
        valid=not issues,
        node_count=int(node_ids.size),
        element_count=int(element_ids.size),
        frame_count=int(times.size),
        time_values_s=tuple(float(value) for value in times),
        mesh_sha256=stored_hash,
        issues=tuple(issues),
    )
