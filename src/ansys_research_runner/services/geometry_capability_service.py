"""G3 official geometry backend capability orchestration and evidence persistence."""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Final

from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.capabilities import (
    CapabilityStatus,
    GeometryBackendCapability,
    GeometryGateCapabilityReport,
)
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.capability_service import run_child_probe, utc_now

REQUIRED_GEOMETRY_CAPABILITIES: Final[tuple[str, ...]] = (
    "body_count",
    "face_count",
    "volume",
    "surface_area",
    "centroid",
    "surface_type",
    "face_orientation",
    "external_face",
    "stable_identity",
    "metamorphic_stability",
    "geometry_graph",
)


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _observation(
    status: CapabilityStatus,
    details: dict[str, object],
    reason: str | None,
) -> dict[str, object]:
    return {"status": status.value, "details": details, "reason": reason}


def _run(argv: list[str], timeout_seconds: float) -> dict[str, object]:
    status, details, reason = run_child_probe(argv, timeout_seconds=timeout_seconds)
    return _observation(status, details, reason)


def _status(observation: object) -> CapabilityStatus:
    if not isinstance(observation, dict):
        return CapabilityStatus.ERROR
    try:
        return CapabilityStatus(str(observation.get("status")))
    except ValueError:
        return CapabilityStatus.ERROR


def _details(observation: object) -> dict[str, object]:
    if not isinstance(observation, dict):
        return {}
    value = observation.get("details")
    return value if isinstance(value, dict) else {}


def _inventory_valid(observation: object, asset: str, expected_faces: int) -> bool:
    if _status(observation) is not CapabilityStatus.AVAILABLE:
        return False
    body = _details(observation).get(asset)
    if not isinstance(body, dict):
        return False
    faces = body.get("faces")
    return (
        body.get("face_count") == expected_faces
        and isinstance(body.get("volume_m3"), int | float)
        and float(body["volume_m3"]) > 0.0
        and isinstance(body.get("surface_area_m2"), int | float)
        and float(body["surface_area_m2"]) > 0.0
        and isinstance(faces, list)
        and len(faces) == expected_faces
        and all(isinstance(face, dict) and face.get("surface_type") for face in faces)
    )


def _centroids_available(observation: object, asset: str) -> bool:
    body = _details(observation).get(asset)
    if not isinstance(body, dict):
        return False
    centroid = body.get("centroid")
    faces = body.get("faces")
    return (
        isinstance(centroid, dict)
        and centroid.get("available") is True
        and isinstance(faces, list)
        and bool(faces)
        and all(
            isinstance(face, dict)
            and isinstance(face.get("centroid"), dict)
            and face["centroid"].get("available") is True
            for face in faces
        )
    )


def _orientation_available(observation: object) -> bool:
    orientation = _details(observation).get("orientation")
    if not isinstance(orientation, dict):
        return False
    result = orientation.get("result")
    return isinstance(result, dict) and result.get("available") is True


def evaluate_discovery_observations(
    observations: dict[str, object],
    *,
    package_version: str | None,
) -> GeometryBackendCapability:
    """Evaluate live Discovery observations without inventing unavailable properties."""

    box_ok = _inventory_valid(observations.get("box_inventory"), "box", 6)
    cylinder_ok = _inventory_valid(observations.get("cylinder_inventory"), "cylinder", 3)
    capabilities: set[str] = set()
    if box_ok and cylinder_ok:
        capabilities.update(
            {
                "body_count",
                "face_count",
                "volume",
                "surface_area",
                "bounding_box",
                "surface_type",
            }
        )
    if (
        box_ok
        and cylinder_ok
        and all(
            (
                _centroids_available(observations.get("box_inventory"), "box"),
                _centroids_available(observations.get("cylinder_inventory"), "cylinder"),
            )
        )
    ):
        capabilities.add("centroid")
    if all(
        (
            _orientation_available(observations.get("box_orientation")),
            _orientation_available(observations.get("cylinder_orientation")),
        )
    ):
        capabilities.add("face_orientation")

    missing = tuple(item for item in REQUIRED_GEOMETRY_CAPABILITIES if item not in capabilities)
    available_details = next(
        (
            _details(item)
            for item in observations.values()
            if _status(item) is CapabilityStatus.AVAILABLE
        ),
        {},
    )
    backend_version = available_details.get("backend_version")
    return GeometryBackendCapability(
        backend="pyansys_geometry_discovery",
        status=CapabilityStatus.AVAILABLE if not missing else CapabilityStatus.BLOCKED,
        package_version=package_version,
        backend_version=str(backend_version) if backend_version is not None else None,
        capabilities=tuple(sorted(capabilities)),
        missing_capabilities=missing,
        reason=None if not missing else "MINIMUM_GEOMETRY_CONTRACT_UNSATISFIED",
        evidence=observations,
    )


def evaluate_prime_observations(
    observations: dict[str, object],
    *,
    package_version: str | None,
) -> GeometryBackendCapability:
    """Evaluate release-matched Prime CAD-import topology observations."""

    expected_faces = {"box": 6, "cylinder": 3}
    topology_ok = True
    for asset, expected in expected_faces.items():
        observation = observations.get(asset)
        details = _details(observation)
        parts = details.get("parts")
        topology_ok = topology_ok and (
            _status(observation) is CapabilityStatus.AVAILABLE
            and details.get("import_error_code") == "NOERROR"
            and details.get("part_count") == 1
            and isinstance(parts, list)
            and len(parts) == 1
            and isinstance(parts[0], dict)
            and isinstance(parts[0].get("summary"), dict)
            and parts[0]["summary"].get("n_topo_faces") == expected
            and parts[0]["summary"].get("n_topo_volumes") == 1
        )
    capabilities = {"body_count", "face_count", "topology_connectivity"} if topology_ok else set()
    missing = tuple(item for item in REQUIRED_GEOMETRY_CAPABILITIES if item not in capabilities)
    return GeometryBackendCapability(
        backend="pyprimemesh",
        status=CapabilityStatus.AVAILABLE if not missing else CapabilityStatus.BLOCKED,
        package_version=package_version,
        backend_version="26.1.0" if topology_ok else None,
        capabilities=tuple(sorted(capabilities)),
        missing_capabilities=missing,
        reason=None if not missing else "MINIMUM_GEOMETRY_CONTRACT_UNSATISFIED",
        evidence=observations,
    )


def _mechanical_fallback(paths: RunnerPaths) -> GeometryBackendCapability:
    capability_path = paths.runtime / "capability_report.json"
    evidence: dict[str, object] = {}
    status = CapabilityStatus.NOT_PROBED
    reason = "G1_CAPABILITY_REPORT_NOT_FOUND"
    if capability_path.is_file():
        payload = json.loads(capability_path.read_text(encoding="utf-8"))
        products = payload.get("products", [])
        if isinstance(products, list):
            mechanical = next(
                (
                    item
                    for item in products
                    if isinstance(item, dict) and item.get("product") == "mechanical"
                ),
                None,
            )
            if isinstance(mechanical, dict):
                evidence = mechanical
                try:
                    status = CapabilityStatus(str(mechanical.get("live_status")))
                except ValueError:
                    status = CapabilityStatus.ERROR
                reason = str(mechanical.get("reason") or "MECHANICAL_GEOMETRY_NOT_AVAILABLE")
    return GeometryBackendCapability(
        backend="mechanical_geometry_fallback",
        status=status,
        package_version=_package_version("ansys-mechanical-core"),
        backend_version="26.1.0",
        capabilities=(),
        missing_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
        reason=reason,
        evidence=evidence,
    )


def collect_geometry_capabilities(
    *, live: bool, probe_timeout_seconds: float = 90.0
) -> GeometryGateCapabilityReport:
    """Probe Geometry, Prime, and the already-isolated Mechanical fallback in order."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    geometry_version = _package_version("ansys-geometry-core")
    prime_version = _package_version("ansys-meshing-prime")
    if not live:
        backends = (
            GeometryBackendCapability(
                backend="pyansys_geometry_discovery",
                status=CapabilityStatus.NOT_PROBED,
                package_version=geometry_version,
                missing_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
                reason="LIVE_PROBE_REQUIRED",
            ),
            GeometryBackendCapability(
                backend="pyansys_geometry_spaceclaim",
                status=CapabilityStatus.NOT_PROBED,
                package_version=geometry_version,
                missing_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
                reason="LIVE_PROBE_REQUIRED",
            ),
            GeometryBackendCapability(
                backend="pyprimemesh",
                status=(
                    CapabilityStatus.NOT_PROBED
                    if prime_version is not None
                    else CapabilityStatus.UNAVAILABLE
                ),
                package_version=prime_version,
                missing_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
                reason="LIVE_PROBE_REQUIRED" if prime_version else "PACKAGE_NOT_INSTALLED",
            ),
            _mechanical_fallback(paths),
        )
    else:
        geometry_workdir = paths.runtime / "probes" / "g3"

        def geometry_probe(
            backend: str, mode: str, asset: str, timeout: float
        ) -> dict[str, object]:
            return _run(
                [
                    sys.executable,
                    "-m",
                    "ansys_research_runner.adapters.geometry.g3_probe_worker",
                    backend,
                    "--mode",
                    mode,
                    "--asset",
                    asset,
                    "--workdir",
                    str((geometry_workdir / f"{backend}-{asset}-{mode}").resolve()),
                ],
                timeout,
            )

        discovery_observations: dict[str, object] = {
            "box_inventory": geometry_probe("discovery", "inventory", "box", probe_timeout_seconds),
            "cylinder_inventory": geometry_probe(
                "discovery", "inventory", "cylinder", probe_timeout_seconds
            ),
            "box_orientation": geometry_probe(
                "discovery", "orientation", "box", probe_timeout_seconds
            ),
            "cylinder_orientation": geometry_probe(
                "discovery", "orientation", "cylinder", probe_timeout_seconds
            ),
        }
        discovery = evaluate_discovery_observations(
            discovery_observations,
            package_version=geometry_version,
        )
        spaceclaim_timeout = min(probe_timeout_seconds, 60.0)
        spaceclaim_observations: dict[str, object] = {
            "box_inventory": geometry_probe("spaceclaim", "inventory", "box", spaceclaim_timeout),
            "cylinder_inventory": geometry_probe(
                "spaceclaim", "inventory", "cylinder", spaceclaim_timeout
            ),
            "box_orientation": geometry_probe(
                "spaceclaim", "orientation", "box", spaceclaim_timeout
            ),
            "cylinder_orientation": geometry_probe(
                "spaceclaim", "orientation", "cylinder", spaceclaim_timeout
            ),
        }
        spaceclaim = evaluate_discovery_observations(
            spaceclaim_observations,
            package_version=geometry_version,
        ).model_copy(update={"backend": "pyansys_geometry_spaceclaim"})

        prime_observations: dict[str, object] = {}
        if prime_version is not None:
            for asset in ("box", "cylinder"):
                asset_path = resource_path("geometry", f"g3_{asset}.step")
                prime_observations[asset] = _run(
                    [
                        sys.executable,
                        "-m",
                        "ansys_research_runner.adapters.geometry.g3_prime_probe_worker",
                        "--route",
                        "programcontrolled",
                        "--asset",
                        str(asset_path.resolve()),
                        "--workdir",
                        str((geometry_workdir / f"prime-{asset}").resolve()),
                    ],
                    probe_timeout_seconds,
                )
            prime = evaluate_prime_observations(
                prime_observations,
                package_version=prime_version,
            )
        else:
            prime = GeometryBackendCapability(
                backend="pyprimemesh",
                status=CapabilityStatus.UNAVAILABLE,
                missing_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
                reason="PACKAGE_NOT_INSTALLED",
            )
        backends = (discovery, spaceclaim, prime, _mechanical_fallback(paths))

    selected = next(
        (
            backend.backend
            for backend in backends
            if backend.status is CapabilityStatus.AVAILABLE and not backend.missing_capabilities
        ),
        None,
    )
    return GeometryGateCapabilityReport(
        generated_at=utc_now(),
        status="PASSED" if selected is not None else "BLOCKED_ENVIRONMENT",
        required_capabilities=REQUIRED_GEOMETRY_CAPABILITIES,
        selected_backend=selected,
        backends=backends,
        blocker_reason=(
            None
            if selected is not None
            else "No installed official backend satisfies the complete Geometry Graph contract."
        ),
    )


def persist_geometry_capabilities(report: GeometryGateCapabilityReport) -> None:
    """Persist machine-local geometry evidence under the ignored runtime root."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    atomic_write_json(
        paths.runtime / "geometry_capability_report.json",
        report.model_dump(mode="json"),
    )
    if report.status == "BLOCKED_ENVIRONMENT":
        atomic_write_json(
            paths.blockers / "G3.json",
            {
                "gate": "G3",
                "status": report.status,
                "failed_contract": list(report.required_capabilities),
                "selected_backend": report.selected_backend,
                "reason": report.blocker_reason,
                "backends": [item.model_dump(mode="json") for item in report.backends],
                "reproduction_command": (
                    "python -m pytest tests/live/test_g3_geometry_live.py -q -m ansys_live"
                ),
            },
        )
