"""Actual local Ansys G3 capability regression."""

import math

import pytest

from ansys_research_runner.domain.capabilities import CapabilityStatus
from ansys_research_runner.services.geometry_capability_service import (
    collect_geometry_capabilities,
    persist_geometry_capabilities,
)


def _cleanup_remaining(evidence: object) -> list[int]:
    if not isinstance(evidence, dict):
        return []
    details = evidence.get("details")
    if not isinstance(details, dict):
        return []
    cleanup = details.get("owned_process_cleanup")
    if not isinstance(cleanup, dict):
        return []
    remaining = cleanup.get("remaining")
    return [int(item) for item in remaining] if isinstance(remaining, list) else []


@pytest.mark.ansys_live
def test_g3_official_geometry_capability_contract() -> None:
    report = collect_geometry_capabilities(live=True, probe_timeout_seconds=90.0)
    persist_geometry_capabilities(report)

    backends = {item.backend: item for item in report.backends}
    discovery = backends["pyansys_geometry_discovery"]
    spaceclaim = backends["pyansys_geometry_spaceclaim"]
    prime = backends["pyprimemesh"]

    assert discovery.status is CapabilityStatus.BLOCKED
    assert {"body_count", "face_count", "volume", "surface_area", "surface_type"}.issubset(
        discovery.capabilities
    )
    assert "centroid" in discovery.missing_capabilities
    assert "face_orientation" in discovery.missing_capabilities
    box = discovery.evidence["box_inventory"]
    cylinder = discovery.evidence["cylinder_inventory"]
    assert isinstance(box, dict) and isinstance(cylinder, dict)
    box_details = box["details"]
    cylinder_details = cylinder["details"]
    assert isinstance(box_details, dict) and isinstance(cylinder_details, dict)
    assert box_details["box"]["face_count"] == 6
    assert math.isclose(box_details["box"]["volume_m3"], 24.0)
    assert cylinder_details["cylinder"]["face_count"] == 3
    assert math.isclose(cylinder_details["cylinder"]["volume_m3"], math.pi / 2.0)

    assert spaceclaim.status is CapabilityStatus.BLOCKED
    assert "face_orientation" in spaceclaim.capabilities
    assert "centroid" in spaceclaim.missing_capabilities
    assert all(observation["status"] == "available" for observation in spaceclaim.evidence.values())

    assert prime.status is CapabilityStatus.BLOCKED
    assert set(prime.capabilities) == {
        "body_count",
        "face_count",
        "topology_connectivity",
    }
    assert all(
        observation["details"]["import_error_code"] == "NOERROR"
        for observation in prime.evidence.values()
    )

    for backend in report.backends:
        for observation in backend.evidence.values():
            assert _cleanup_remaining(observation) == []

    assert report.status == "BLOCKED_ENVIRONMENT"
    assert report.selected_backend is None
