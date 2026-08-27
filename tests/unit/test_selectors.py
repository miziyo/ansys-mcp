"""G2 semantic selector and ambiguity-policy tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import (
    TestGeometrySpec as GeometrySpec,
)
from ansys_research_runner.adapters.geometry.synthetic import split_planar_face, synthetic_graph
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.selectors import (
    ResolutionStatus,
    RoleDefinition,
    SelectorExpression,
    resolve_regions,
    selector_json,
)
from tests.g2_fixtures import box_graph, box_manifest


def _role(
    entity: str,
    cardinality: str,
    selector: dict[str, object],
    *,
    count: int | None = None,
) -> RoleDefinition:
    payload: dict[str, object] = {
        "entity": entity,
        "cardinality": cardinality,
        "selector": selector,
    }
    if count is not None:
        payload["count"] = count
    return RoleDefinition.model_validate(payload)


def test_box_manifest_resolves_hot_cold_and_exterior() -> None:
    manifest = box_manifest()
    result = resolve_regions(box_graph(), manifest.roles, manifest.coordinate_frame)
    assert result.successful
    assert result.roles["hot_boundary"].candidate_keys == ("box.face.x_max",)
    assert result.roles["cold_boundary"].candidate_keys == ("box.face.x_min",)
    assert result.roles["exterior"].selected_count == 6
    evidence = result.roles["hot_boundary"].evidence[0]
    assert evidence.internal_runtime_id == "runtime:box:face:x_max"
    assert evidence.measurements["surface_type"] == "planar"
    assert evidence.geometry_fingerprint == result.geometry_fingerprint


def test_cylinder_planar_ends_and_cylindrical_side_are_distinct() -> None:
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER))
    roles = {
        "domain": _role("body", "exactly_one", {"solid_body": True}),
        "ends": _role(
            "face",
            "exactly_n",
            {
                "all": [
                    {"external_of": "domain"},
                    {"surface_type": "planar"},
                ]
            },
            count=2,
        ),
        "side": _role(
            "face",
            "exactly_one",
            {
                "all": [
                    {"parent_body_role": "domain"},
                    {"surface_type": "cylindrical"},
                ]
            },
        ),
    }
    result = resolve_regions(graph, roles)
    assert result.successful
    assert result.roles["ends"].selected_count == 2
    assert result.roles["side"].candidate_keys == ("cylinder.face.side",)


def test_ambiguous_symmetric_geometry_never_chooses_one_candidate() -> None:
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.AMBIGUOUS_SYMMETRIC))
    roles = {
        "domain": _role("body", "one_or_more", {"solid_body": True}),
        "hot": _role(
            "face",
            "exactly_one",
            {
                "all": [
                    {"parent_body_role": "domain"},
                    {"centroid_extreme": {"axis": "local_x", "side": "maximum"}},
                ]
            },
        ),
    }
    result = resolve_regions(graph, roles)
    ambiguous = result.roles["hot"]
    assert ambiguous.status is ResolutionStatus.AMBIGUOUS
    assert ambiguous.selected_count == 2
    assert ambiguous.evidence == ()
    assert ambiguous.error is not None
    assert ambiguous.error.code is ErrorCode.AMBIGUOUS_ROLE


def test_face_split_preserves_all_matches_and_never_picks_one_fragment() -> None:
    graph = split_planar_face(box_graph(), "box.face.x_max")
    selector = {
        "all": [
            {"surface_type": "planar"},
            {"centroid_extreme": {"axis": "local_x", "side": "maximum"}},
        ]
    }
    many = resolve_regions(graph, {"hot": _role("face", "one_or_more", selector)}).roles["hot"]
    assert many.status is ResolutionStatus.RESOLVED
    assert many.selected_count == 2
    exactly_one = resolve_regions(graph, {"hot": _role("face", "exactly_one", selector)}).roles[
        "hot"
    ]
    assert exactly_one.status is ResolutionStatus.AMBIGUOUS
    assert exactly_one.evidence == ()


def test_zero_candidate_required_role_is_unresolved() -> None:
    roles = {"missing": _role("face", "exactly_one", {"name_regex": "does-not-exist"})}
    result = resolve_regions(box_graph(), roles)
    assert result.roles["missing"].status is ResolutionStatus.UNRESOLVED
    assert result.roles["missing"].error is not None
    assert result.roles["missing"].error.code is ErrorCode.UNRESOLVED_ROLE


@pytest.mark.parametrize(
    ("cardinality", "count", "expected"),
    [
        ("zero_or_one", None, ResolutionStatus.RESOLVED),
        ("zero_or_more", None, ResolutionStatus.RESOLVED),
        ("exactly_n", 0, ResolutionStatus.RESOLVED),
        ("one_or_more", None, ResolutionStatus.UNRESOLVED),
    ],
)
def test_zero_selection_cardinality(
    cardinality: str, count: int | None, expected: ResolutionStatus
) -> None:
    result = resolve_regions(
        box_graph(),
        {"role": _role("face", cardinality, {"name_regex": "^never$"}, count=count)},
    )
    assert result.roles["role"].status is expected


def test_logical_any_not_and_name_regex() -> None:
    selector = {
        "all": [
            {"any": [{"name_regex": "_x_"}, {"name_regex": "_y_"}]},
            {"not": {"name_regex": "_min$"}},
        ]
    }
    result = resolve_regions(
        box_graph(), {"selected": _role("face", "exactly_n", selector, count=2)}
    )
    assert result.roles["selected"].candidate_keys == (
        "box.face.x_max",
        "box.face.y_max",
    )


def test_body_predicates_cover_largest_range_side_and_nearest() -> None:
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.MULTI_BODY_BOX))
    roles = {
        "largest": _role("body", "exactly_one", {"largest_volume": True}),
        "relative": _role(
            "body",
            "exactly_one",
            {"volume_relative_range": {"minimum": 0.9, "maximum": 1.0}},
        ),
        "positive": _role(
            "body",
            "exactly_one",
            {"centroid_side": {"axis": "local_x", "side": "positive"}},
        ),
        "nearest": _role(
            "body",
            "exactly_one",
            {"nearest_to_point": {"point": [-2.1, 0, 0]}},
        ),
    }
    result = resolve_regions(graph, roles)
    assert result.successful
    assert result.roles["largest"].candidate_keys == ("large.body",)
    assert result.roles["relative"].candidate_keys == ("large.body",)
    assert result.roles["positive"].candidate_keys == ("large.body",)
    assert result.roles["nearest"].candidate_keys == ("small.body",)


def test_face_area_range_and_centroid_percentile() -> None:
    roles = {
        "large_faces": _role(
            "face",
            "one_or_more",
            {"area_relative_range": {"minimum": 0.99, "maximum": 1.0}},
        ),
        "upper": _role(
            "face",
            "one_or_more",
            {
                "centroid_percentile": {
                    "axis": "local_z",
                    "minimum": 99,
                    "maximum": 100,
                }
            },
        ),
    }
    result = resolve_regions(box_graph(), roles)
    assert result.roles["large_faces"].selected_count == 2
    assert result.roles["upper"].candidate_keys == ("box.face.z_max",)


def test_normal_capability_failure_is_reported() -> None:
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.CYLINDER))
    role = _role(
        "face",
        "one_or_more",
        {"normal_parallel_to": {"axis": "local_x", "tolerance_deg": 5}},
    )
    result = resolve_regions(graph, {"faces": role})
    assert result.roles["faces"].status is ResolutionStatus.UNSUPPORTED
    assert result.roles["faces"].error is not None
    assert result.roles["faces"].error.code is ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY


def test_source_bound_named_selection_resolves_without_centroids() -> None:
    original = box_graph()
    graph = original.model_copy(
        update={
            "bodies": tuple(
                body.model_copy(update={"centroid": None, "named_selections": ("THERMAL_DOMAIN",)})
                for body in original.bodies
            ),
            "faces": tuple(
                face.model_copy(
                    update={
                        "centroid": None,
                        "named_selections": (
                            ("HOT_FACE",) if face.stable_key == "box.face.x_max" else ()
                        ),
                    }
                )
                for face in original.faces
            ),
        }
    )
    roles = {
        "domain": _role("body", "exactly_one", {"named_selection": "THERMAL_DOMAIN"}),
        "hot": _role("face", "exactly_one", {"named_selection": "HOT_FACE"}),
    }

    result = resolve_regions(graph, roles)

    assert result.successful
    assert result.roles["hot"].candidate_keys == ("box.face.x_max",)
    assert result.roles["hot"].evidence[0].measurements["centroid_m"] is None


def test_source_bound_bounding_box_extreme_is_exact_and_ties_are_ambiguous() -> None:
    original = box_graph()
    graph = original.model_copy(
        update={
            "bodies": tuple(body.model_copy(update={"centroid": None}) for body in original.bodies),
            "faces": tuple(face.model_copy(update={"centroid": None}) for face in original.faces),
        }
    )
    maximum_x = {"bounding_box_extreme": {"axis": "x", "side": "maximum"}}

    resolved = resolve_regions(graph, {"hot": _role("face", "exactly_one", maximum_x)}).roles["hot"]

    # Four side faces also reach the global x maximum, so the resolver must not
    # pretend that a bounding-box extreme alone identifies the planar end face.
    assert resolved.status is ResolutionStatus.AMBIGUOUS
    assert resolved.selected_count == 5

    constrained = resolve_regions(
        graph,
        {
            "hot": _role(
                "face",
                "exactly_one",
                {"all": [{"surface_type": "planar"}, maximum_x]},
            )
        },
    ).roles["hot"]
    assert constrained.status is ResolutionStatus.AMBIGUOUS

    normal_constrained = resolve_regions(
        graph,
        {
            "hot": _role(
                "face",
                "exactly_one",
                {
                    "all": [
                        {"normal_parallel_to": {"axis": "local_x", "tolerance_deg": 1}},
                        maximum_x,
                    ]
                },
            )
        },
    ).roles["hot"]
    assert normal_constrained.status is ResolutionStatus.RESOLVED
    assert normal_constrained.candidate_keys == ("box.face.x_max",)


def test_source_bound_centroid_selector_is_explicitly_unsupported() -> None:
    original = box_graph()
    graph = original.model_copy(
        update={
            "faces": tuple(face.model_copy(update={"centroid": None}) for face in original.faces)
        }
    )
    result = resolve_regions(
        graph,
        {
            "hot": _role(
                "face",
                "exactly_one",
                {"centroid_extreme": {"axis": "local_x", "side": "maximum"}},
            )
        },
    ).roles["hot"]

    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_SELECTOR_CAPABILITY


def test_invalid_regex_is_reported_without_crash() -> None:
    result = resolve_regions(
        box_graph(), {"bad": _role("face", "zero_or_more", {"name_regex": "["})}
    )
    assert result.roles["bad"].status is ResolutionStatus.UNSUPPORTED
    assert result.roles["bad"].error is not None
    assert result.roles["bad"].error.code is ErrorCode.INVALID_SELECTOR


def test_unknown_role_and_cycle_are_structured() -> None:
    with pytest.raises(DomainError) as unknown:
        resolve_regions(
            box_graph(),
            {"faces": _role("face", "one_or_more", {"external_of": "missing"})},
        )
    assert unknown.value.code is ErrorCode.UNKNOWN_ROLE
    roles = {
        "a": _role("face", "zero_or_more", {"parent_body_role": "b"}),
        "b": _role("face", "zero_or_more", {"parent_body_role": "a"}),
    }
    with pytest.raises(DomainError) as cycle:
        resolve_regions(box_graph(), roles)
    assert cycle.value.code is ErrorCode.ROLE_DEPENDENCY_CYCLE


def test_selector_ast_rejects_unknown_or_arbitrary_code() -> None:
    with pytest.raises(ValidationError):
        SelectorExpression.model_validate({"python": "eval('danger')"})
    with pytest.raises(ValidationError):
        SelectorExpression.model_validate({"solid_body": True, "exec": "danger"})


def test_cardinality_model_requires_exactly_n_count() -> None:
    with pytest.raises(ValidationError, match="requires count"):
        _role("face", "exactly_n", {"interface": False})
    with pytest.raises(ValidationError, match="only valid"):
        _role("face", "exactly_one", {"interface": False}, count=1)


def test_selector_serialization_is_deterministic() -> None:
    expression = SelectorExpression.model_validate(
        {"all": [{"surface_type": "planar"}, {"interface": False}]}
    )
    assert selector_json(expression) == selector_json(expression)
    assert selector_json(expression).startswith('{"all":')
