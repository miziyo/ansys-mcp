"""Fail-safe official PyAnsys Geometry adapters for full and source-bound evidence."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ansys_research_runner.adapters.geometry.base import (
    ArtifactRecord,
    GeometryCapabilityReport,
    GeometryInspectionRequest,
    TestGeometryKind,
    TestGeometrySpec,
)
from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.capabilities import (
    CapabilityStatus,
    GeometryBackendCapability,
    GeometryGateCapabilityReport,
)
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import (
    BodyDescriptor,
    BoundingBox,
    FaceDescriptor,
    GeometryCapabilityTier,
    GeometryGraph,
    SurfaceType,
    Vector3,
)
from ansys_research_runner.domain.selectors import RegionResolution
from ansys_research_runner.domain.units import PhysicalDimension, quantity_from_si
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.capability_service import (
    resolve_ansys_root,
    run_child_probe,
)

_SOURCE_BOUND_EXTENSIONS = frozenset(
    {
        ".step",
        ".stp",
        ".x_t",
        ".x_b",
        ".parasolid",
        ".scdoc",
        ".scdocx",
        ".dsco",
        ".pmdb",
    }
)
_SOURCE_BOUND_REQUIRED = (
    "body_count",
    "face_count",
    "volume",
    "surface_area",
    "bounding_box",
    "surface_type",
    "face_orientation",
    "source_sha256",
    "source_bound_identity",
    "geometry_graph",
)


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _canonical_number(value: object) -> str | None:
    if isinstance(value, int | float):
        return format(float(value), ".15g")
    return None


def _canonical_vector(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    numbers = tuple(_canonical_number(item) for item in value)
    if any(item is None for item in numbers):
        return None
    return tuple(str(item) for item in numbers)


def _vector(value: object) -> Vector3 | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    return Vector3((float(value[0]), float(value[1]), float(value[2])))


def _required_vector(value: object, path: str) -> Vector3:
    vector = _vector(value)
    if vector is None:
        raise DomainError(
            ErrorCode.GEOMETRY_CAPABILITY_MISSING,
            path,
            "Geometry worker omitted an exact three-component vector.",
        )
    return vector


def _names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(sorted({str(item) for item in value if str(item)}))


def _surface_type(value: object) -> SurfaceType:
    name = str(value).upper()
    if name.endswith("PLANE"):
        return SurfaceType.PLANAR
    if name.endswith("CYLINDER"):
        return SurfaceType.CYLINDRICAL
    if name.endswith("CONE"):
        return SurfaceType.CONICAL
    if name.endswith("SPHERE"):
        return SurfaceType.SPHERICAL
    if name:
        return SurfaceType.OTHER
    return SurfaceType.UNKNOWN


def _stable_hash(prefix: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


class PyAnsysGeometryAdapter:
    """Use a full backend when available or a safe v261 source-bound exact tier."""

    def __init__(
        self,
        capability: GeometryBackendCapability | None = None,
        *,
        inspect_timeout_seconds: float = 240.0,
    ) -> None:
        self._explicit_capability = capability is not None
        self._capability = capability or self._load_spaceclaim_capability()
        self._inspect_timeout_seconds = inspect_timeout_seconds
        self._paths = RunnerPaths.from_environment()
        self._closed = False

    @staticmethod
    def _load_spaceclaim_capability() -> GeometryBackendCapability | None:
        report_path = RunnerPaths.from_environment().runtime / "geometry_capability_report.json"
        if not report_path.is_file():
            return None
        report = GeometryGateCapabilityReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
        return next(
            (item for item in report.backends if item.backend == "pyansys_geometry_spaceclaim"),
            None,
        )

    @staticmethod
    def _spaceclaim_executable() -> Path:
        return (resolve_ansys_root() / "scdm" / "SpaceClaim.exe").resolve()

    def probe_capabilities(self) -> GeometryCapabilityReport:
        """Report a complete graph backend or the narrower source-bound exact tier."""

        if self._closed:
            return GeometryCapabilityReport(
                backend="pyansys_geometry_spaceclaim_source_bound",
                available=False,
                capabilities=(),
                reason="ADAPTER_CLOSED",
            )
        capability = self._capability
        full_ready = (
            capability is not None
            and capability.status is CapabilityStatus.AVAILABLE
            and not capability.missing_capabilities
        )
        if full_ready:
            assert capability is not None
            return GeometryCapabilityReport(
                backend=capability.backend,
                available=True,
                capabilities=capability.capabilities,
                required_capabilities=capability.capabilities,
                package_version=capability.package_version,
                backend_version=capability.backend_version,
                evidence=capability.evidence,
            )
        if self._explicit_capability:
            return GeometryCapabilityReport(
                backend=(
                    capability.backend if capability is not None else "pyansys_geometry_spaceclaim"
                ),
                available=False,
                capabilities=() if capability is None else capability.capabilities,
                required_capabilities=(
                    ()
                    if capability is None
                    else tuple(
                        sorted(set(capability.capabilities) | set(capability.missing_capabilities))
                    )
                ),
                missing_capabilities=(
                    () if capability is None else capability.missing_capabilities
                ),
                package_version=None if capability is None else capability.package_version,
                backend_version=None if capability is None else capability.backend_version,
                evidence={} if capability is None else capability.evidence,
                reason="LIVE_PROBE_REQUIRED" if capability is None else capability.reason,
            )
        package_version = _package_version("ansys-geometry-core")
        executable = self._spaceclaim_executable()
        available = package_version is not None and executable.is_file()
        full_missing = () if capability is None else capability.missing_capabilities
        return GeometryCapabilityReport(
            backend="pyansys_geometry_spaceclaim_source_bound",
            available=available,
            capabilities=_SOURCE_BOUND_REQUIRED if available else (),
            required_capabilities=_SOURCE_BOUND_REQUIRED,
            missing_capabilities=full_missing,
            package_version=package_version,
            backend_version=None if capability is None else capability.backend_version,
            evidence={
                "spaceclaim_executable": str(executable),
                "capability_tier": GeometryCapabilityTier.SOURCE_BOUND_EXACT.value,
                "full_semantic_missing": list(full_missing),
            },
            reason=(
                "SOURCE_BOUND_EXACT_ONLY"
                if available
                else "SPACECLAIM_OR_PYANSYS_GEOMETRY_UNAVAILABLE"
            ),
        )

    def _run_worker(self, arguments: list[str], *, operation: str) -> dict[str, object]:
        status, details, reason = run_child_probe(
            [
                sys.executable,
                "-m",
                "ansys_research_runner.adapters.geometry.source_bound_worker",
                *arguments,
            ],
            timeout_seconds=self._inspect_timeout_seconds,
        )
        if status is not CapabilityStatus.AVAILABLE:
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                operation,
                "Source-bound exact Geometry inspection did not complete.",
                details={
                    "status": status.value,
                    "reason": reason,
                    "evidence": details,
                },
            )
        return details

    @staticmethod
    def _body_descriptor(
        body: dict[str, Any], source: Path, source_sha256: str
    ) -> tuple[BodyDescriptor, str]:
        minimum = _required_vector(body.get("bounding_box_min_m"), "geometry.body.bounding_box")
        maximum = _required_vector(body.get("bounding_box_max_m"), "geometry.body.bounding_box")
        names = _names(body.get("named_selections"))
        signature: dict[str, object] = {
            "source_sha256": source_sha256,
            "display_name": str(body.get("display_name") or "solid"),
            "volume_m3": _canonical_number(body.get("volume_m3")),
            "surface_area_m2": _canonical_number(body.get("surface_area_m2")),
            "minimum": _canonical_vector(body.get("bounding_box_min_m")),
            "maximum": _canonical_vector(body.get("bounding_box_max_m")),
            "named_selections": names,
        }
        stable_key = _stable_hash("source-body", signature)
        dimension_values = tuple(
            quantity_from_si(abs(high - low), PhysicalDimension.LENGTH)
            for low, high in zip(minimum.root, maximum.root, strict=True)
        )
        dimensions = (dimension_values[0], dimension_values[1], dimension_values[2])
        descriptor = BodyDescriptor(
            stable_key=stable_key,
            internal_runtime_id=str(body["runtime_id"]),
            display_name=str(body.get("display_name") or "solid"),
            volume=quantity_from_si(float(body["volume_m3"]), PhysicalDimension.VOLUME),
            surface_area=quantity_from_si(float(body["surface_area_m2"]), PhysicalDimension.AREA),
            centroid=_vector(body.get("centroid_m")),
            bounding_box=BoundingBox(minimum=minimum, maximum=maximum),
            principal_dimensions=dimensions,
            principal_axes=None,
            named_selections=names,
            source_path=str(source),
            metadata={
                "identity_scope": "exact_source_sha256",
                "source_sha256": source_sha256,
            },
        )
        return descriptor, stable_key

    @staticmethod
    def _face_descriptors(
        faces: list[dict[str, Any]], parent_body_key: str, source_sha256: str
    ) -> tuple[FaceDescriptor, ...]:
        grouped: dict[str, list[tuple[dict[str, Any], dict[str, object]]]] = {}
        for face in faces:
            names = _names(face.get("named_selections"))
            signature: dict[str, object] = {
                "source_sha256": source_sha256,
                "parent_body_key": parent_body_key,
                "surface_type": str(face.get("surface_type")),
                "area_m2": _canonical_number(face.get("area_m2")),
                "minimum": _canonical_vector(face.get("bounding_box_min_m")),
                "maximum": _canonical_vector(face.get("bounding_box_max_m")),
                "normal": _canonical_vector(face.get("normal")),
                "axis": _canonical_vector(face.get("axis")),
                "named_selections": names,
            }
            base_key = _stable_hash("source-face", signature)
            grouped.setdefault(base_key, []).append((face, signature))

        descriptors: list[FaceDescriptor] = []
        for base_key in sorted(grouped):
            members = sorted(grouped[base_key], key=lambda item: str(item[0]["runtime_id"]))
            count = len(members)
            for index, (face, signature) in enumerate(members, start=1):
                stable_key = base_key if count == 1 else f"{base_key}:equivalent-{index}-of-{count}"
                minimum = _required_vector(
                    face.get("bounding_box_min_m"), "geometry.face.bounding_box"
                )
                maximum = _required_vector(
                    face.get("bounding_box_max_m"), "geometry.face.bounding_box"
                )
                names = _names(face.get("named_selections"))
                display_name = names[0] if names else f"Face-{base_key.rsplit(':', 1)[-1][:12]}"
                descriptors.append(
                    FaceDescriptor(
                        stable_key=stable_key,
                        internal_runtime_id=str(face["runtime_id"]),
                        parent_body_key=parent_body_key,
                        display_name=display_name,
                        surface_type=_surface_type(face.get("surface_type")),
                        area=quantity_from_si(float(face["area_m2"]), PhysicalDimension.AREA),
                        centroid=_vector(face.get("centroid_m")),
                        normal=_vector(face.get("normal")),
                        axis=_vector(face.get("axis")),
                        external=True,
                        interface=False,
                        bounding_box=BoundingBox(minimum=minimum, maximum=maximum),
                        named_selections=names,
                        metadata={
                            "identity_scope": "exact_source_sha256",
                            "signature": signature,
                            "equivalent_member_count": count,
                        },
                    )
                )
        return tuple(sorted(descriptors, key=lambda item: item.stable_key))

    def inspect(self, request: GeometryInspectionRequest) -> GeometryGraph:
        """Inspect one immutable single-solid source without fabricating centroids."""

        if self._closed:
            raise RuntimeError("Geometry adapter is closed.")
        capability = self.probe_capabilities()
        if not capability.available:
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.inspect",
                "Configured Geometry adapter is unavailable.",
                details={
                    "backend": capability.backend,
                    "missing_capabilities": list(capability.missing_capabilities),
                    "reason": capability.reason,
                },
            )
        source = request.model_path.resolve(strict=True)
        if source.suffix.lower() not in _SOURCE_BOUND_EXTENSIONS:
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.inspect",
                "Source-bound exact inspection does not support this CAD extension.",
                details={
                    "extension": source.suffix.lower(),
                    "supported": sorted(_SOURCE_BOUND_EXTENSIONS),
                },
            )
        workdir = (
            self._paths.runtime / "probes" / "source-bound" / f"inspect-{uuid.uuid4().hex}"
        ).resolve()
        details = self._run_worker(
            ["--workdir", str(workdir), "--source", str(source)],
            operation="geometry_adapter.inspect",
        )
        raw_bodies = details.get("bodies")
        if (
            not isinstance(raw_bodies, list)
            or len(raw_bodies) != 1
            or not isinstance(raw_bodies[0], dict)
        ):
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.inspect",
                "Source-bound exact inspection requires one solid body.",
                details={"body_count": 0 if not isinstance(raw_bodies, list) else len(raw_bodies)},
            )
        source_sha256 = str(details.get("source_sha256"))
        if len(source_sha256) != 64:
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.inspect",
                "Geometry worker omitted the source SHA-256.",
            )
        raw_body = raw_bodies[0]
        body, body_key = self._body_descriptor(raw_body, source, source_sha256)
        raw_faces = raw_body.get("faces")
        if (
            not isinstance(raw_faces, list)
            or not raw_faces
            or not all(isinstance(face, dict) for face in raw_faces)
        ):
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.inspect",
                "Geometry worker returned no inspectable faces.",
            )
        faces = self._face_descriptors(raw_faces, body_key, source_sha256)
        return GeometryGraph(
            schema_version=2,
            source_path=str(source),
            source_sha256=source_sha256,
            capability_tier=GeometryCapabilityTier.SOURCE_BOUND_EXACT,
            bodies=(body,),
            faces=faces,
            metadata={
                "backend": details.get("backend"),
                "backend_version": details.get("backend_version"),
                "package_version": details.get("package_version"),
                "named_selections": details.get("named_selections", []),
                "identity_scope": "exact_source_sha256",
                "owned_process_cleanup": details.get("owned_process_cleanup", {}),
            },
        )

    def generate_test_asset(self, spec: TestGeometrySpec, output_dir: Path) -> Path:
        """Generate a reviewed named-selection box for live source-bound qualification."""

        if self._closed:
            raise RuntimeError("Geometry adapter is closed.")
        if spec.kind is not TestGeometryKind.BOX:
            raise DomainError(
                ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                "geometry_adapter.generate_test_asset",
                "Source-bound live qualification currently generates only a box.",
                details={"kind": spec.kind.value},
            )
        output = (output_dir.resolve() / "source-bound-box.scdocx").resolve()
        workdir = (
            self._paths.runtime / "probes" / "source-bound" / f"generate-{uuid.uuid4().hex}"
        ).resolve()
        self._run_worker(
            [
                "--workdir",
                str(workdir),
                "--create-box",
                str(output),
                "--dimensions-m",
                *(format(value, ".17g") for value in spec.dimensions_m),
            ],
            operation="geometry_adapter.generate_test_asset",
        )
        return output

    def create_selection_preview(
        self,
        graph: GeometryGraph,
        resolution: RegionResolution,
        output_dir: Path,
    ) -> list[ArtifactRecord]:
        """Persist deterministic selection evidence without a live product object."""

        payload = {
            "geometry_fingerprint": graph.fingerprint(),
            "capability_tier": graph.capability_tier.value,
            "resolution": resolution.model_dump(mode="json", by_alias=True),
            "source": "pyansys_geometry",
        }
        path = output_dir / "selection-preview.json"
        atomic_write_json(path, payload)
        return [
            ArtifactRecord(
                path=path,
                media_type="application/json",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        ]

    def close(self) -> None:
        """Close the adapter; live products are owned by bounded child workers."""

        self._closed = True
