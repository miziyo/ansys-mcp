"""Hypothesis metamorphic properties required by G2."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import (
    TestGeometrySpec as GeometrySpec,
)
from ansys_research_runner.adapters.geometry.synthetic import (
    synthetic_graph,
    transform_frame,
    transform_graph,
)
from ansys_research_runner.domain.geometry import Vector3
from ansys_research_runner.domain.selectors import (
    ResolutionStatus,
    RoleDefinition,
    resolve_regions,
)
from ansys_research_runner.domain.units import PhysicalDimension, parse_quantity
from ansys_research_runner.services.contract_service import deterministic_json
from tests.g2_fixtures import box_graph, box_manifest, steady_recipe

finite_coordinate = st.floats(
    min_value=-1000.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)
positive_scale = st.floats(
    min_value=0.01,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
)


def _selected_keys(result: object, role: str) -> tuple[str, ...]:
    return result.roles[role].candidate_keys  # type: ignore[attr-defined,no-any-return]


@given(
    x=finite_coordinate,
    y=finite_coordinate,
    z=finite_coordinate,
)
@settings(max_examples=30, deadline=None)
def test_translation_preserves_directional_role_resolution(x: float, y: float, z: float) -> None:
    manifest = box_manifest()
    baseline = resolve_regions(box_graph(), manifest.roles, manifest.coordinate_frame)
    moved = transform_graph(box_graph(), translation=Vector3((x, y, z)))
    moved_frame = manifest.coordinate_frame.model_copy(update={"origin": Vector3((x, y, z))})
    result = resolve_regions(moved, manifest.roles, moved_frame)
    assert _selected_keys(result, "hot_boundary") == _selected_keys(baseline, "hot_boundary")
    assert _selected_keys(result, "cold_boundary") == _selected_keys(baseline, "cold_boundary")


@given(scale=positive_scale)
@settings(max_examples=30, deadline=None)
def test_uniform_scale_preserves_selector_roles(scale: float) -> None:
    manifest = box_manifest()
    baseline = resolve_regions(box_graph(), manifest.roles, manifest.coordinate_frame)
    scaled = transform_graph(box_graph(), scale=scale)
    result = resolve_regions(scaled, manifest.roles, manifest.coordinate_frame)
    for role in manifest.roles:
        assert _selected_keys(result, role) == _selected_keys(baseline, role)


@given(prefix=st.text(alphabet="abcdef0123456789", min_size=1, max_size=16))
@settings(max_examples=25, deadline=None)
def test_runtime_id_and_entity_order_do_not_change_fingerprint_or_selection(
    prefix: str,
) -> None:
    graph = box_graph()
    manifest = box_manifest()
    remapped = transform_graph(graph, runtime_prefix=prefix, reverse_order=True)
    baseline = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    changed = resolve_regions(remapped, manifest.roles, manifest.coordinate_frame)
    assert remapped.fingerprint() == graph.fingerprint()
    for role in manifest.roles:
        assert _selected_keys(changed, role) == _selected_keys(baseline, role)


def test_explicit_frame_rotation_invariance() -> None:
    rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    translation = Vector3((3.0, -4.0, 2.0))
    manifest = box_manifest()
    baseline = resolve_regions(box_graph(), manifest.roles, manifest.coordinate_frame)
    rotated_graph = transform_graph(
        box_graph(), matrix=rotation, translation=translation, runtime_prefix="rotated"
    )
    rotated_frame = transform_frame(
        manifest.coordinate_frame,
        matrix=rotation,
        translation=translation,
    )
    rotated = resolve_regions(rotated_graph, manifest.roles, rotated_frame)
    for role in manifest.roles:
        assert _selected_keys(rotated, role) == _selected_keys(baseline, role)


@given(
    value=st.floats(
        min_value=1.0e-9,
        max_value=1.0e9,
        allow_nan=False,
        allow_infinity=False,
    )
)
@settings(max_examples=40, deadline=None)
def test_unit_round_trip_property(value: float) -> None:
    millimeters = parse_quantity(f"{value:.17g} mm", PhysicalDimension.LENGTH)
    meters = parse_quantity(f"{millimeters.si_value:.17g} m", PhysicalDimension.LENGTH)
    assert meters.si_value == pytest.approx(millimeters.si_value, rel=1.0e-14)


@given(expected_count=st.integers(min_value=0, max_value=4))
def test_exactly_n_cardinality_never_arbitrarily_truncates(expected_count: int) -> None:
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.MULTI_BODY_BOX))
    role = RoleDefinition.model_validate(
        {
            "entity": "body",
            "cardinality": "exactly_n",
            "count": expected_count,
            "selector": {"solid_body": True},
        }
    )
    result = resolve_regions(graph, {"domain": role}).roles["domain"]
    if expected_count == 2:
        assert result.status is ResolutionStatus.RESOLVED
        assert len(result.evidence) == 2
    else:
        assert result.status in {ResolutionStatus.AMBIGUOUS, ResolutionStatus.UNRESOLVED}
        assert result.evidence == ()
        assert result.selected_count == 2


@given(reverse=st.booleans())
def test_deterministic_json_serialization(reverse: bool) -> None:
    recipe = steady_recipe()
    payload = recipe.model_dump(mode="json", by_alias=True)
    if reverse:
        payload["materials"] = dict(reversed(list(payload["materials"].items())))
    restored = type(recipe).model_validate(payload)
    serialized = deterministic_json(restored)
    assert serialized == json.dumps(json.loads(serialized), separators=(",", ":"), sort_keys=True)
