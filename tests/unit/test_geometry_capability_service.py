"""Unit tests for G3 capability evaluation and persistence formatting."""

from ansys_research_runner.domain.capabilities import CapabilityStatus
from ansys_research_runner.services.geometry_capability_service import (
    evaluate_discovery_observations,
    evaluate_prime_observations,
)


def _inventory(asset: str, face_count: int, *, centroids: bool) -> dict[str, object]:
    return {
        "status": "available",
        "details": {
            "backend_version": "26.1.0",
            asset: {
                "face_count": face_count,
                "volume_m3": 1.0,
                "surface_area_m2": 2.0,
                "centroid": {"available": centroids},
                "faces": [
                    {
                        "surface_type": "SURFACETYPE_PLANE",
                        "centroid": {"available": centroids},
                    }
                    for _ in range(face_count)
                ],
            },
        },
        "reason": None,
    }


def _orientation(available: bool) -> dict[str, object]:
    return {
        "status": "available",
        "details": {"orientation": {"result": {"available": available}}},
        "reason": None,
    }


def _prime(asset: str, face_count: int) -> dict[str, object]:
    return {
        "status": "available",
        "details": {
            "import_error_code": "NOERROR",
            "part_count": 1,
            "parts": [
                {
                    "display_name": asset,
                    "summary": {"n_topo_faces": face_count, "n_topo_volumes": 1},
                }
            ],
        },
        "reason": None,
    }


def test_discovery_evaluation_preserves_partial_capabilities_without_fake_values() -> None:
    backend = evaluate_discovery_observations(
        {
            "box_inventory": _inventory("box", 6, centroids=False),
            "cylinder_inventory": _inventory("cylinder", 3, centroids=False),
            "box_orientation": _orientation(False),
            "cylinder_orientation": _orientation(False),
        },
        package_version="0.17.1",
    )

    assert backend.status is CapabilityStatus.BLOCKED
    assert set(backend.capabilities) == {
        "body_count",
        "bounding_box",
        "face_count",
        "surface_area",
        "surface_type",
        "volume",
    }
    assert "centroid" in backend.missing_capabilities
    assert "face_orientation" in backend.missing_capabilities
    assert "geometry_graph" in backend.missing_capabilities


def test_prime_evaluation_accepts_topology_counts_but_not_geometry_measures() -> None:
    backend = evaluate_prime_observations(
        {"box": _prime("box", 6), "cylinder": _prime("cylinder", 3)},
        package_version="0.10.4",
    )

    assert backend.status is CapabilityStatus.BLOCKED
    assert set(backend.capabilities) == {
        "body_count",
        "face_count",
        "topology_connectivity",
    }
    assert "volume" in backend.missing_capabilities
    assert "centroid" in backend.missing_capabilities
