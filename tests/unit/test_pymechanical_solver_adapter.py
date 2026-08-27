"""PyMechanical adapter fail-safe and source-identity tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ansys_research_runner.adapters.geometry.base import (
    TestGeometryKind as GeometryKind,
)
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import synthetic_graph
from ansys_research_runner.adapters.solver.base import RunCallbacks
from ansys_research_runner.adapters.solver.pymechanical import PyMechanicalSolverAdapter
from ansys_research_runner.domain.results import ExecutionStatus
from ansys_research_runner.domain.selectors import resolve_regions
from ansys_research_runner.services.compilation_service import compile_cae_ir
from tests.g2_fixtures import box_manifest, mesh_policy, steady_blueprint, steady_recipe


def test_adapter_refuses_blocked_live_capability_without_launch(tmp_path: Path) -> None:
    source = tmp_path / "box.step"
    source.write_bytes(b"safe-source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.BOX)).model_copy(
        update={"source_path": str(source), "source_sha256": digest}
    )
    manifest = box_manifest().model_copy(
        update={"model": box_manifest().model.model_copy(update={"file": str(source)})}
    )
    recipe = steady_recipe()
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id="blocked-mechanical",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=recipe,
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy(),
    )
    capability = tmp_path / "capability.json"
    capability.write_text(
        json.dumps(
            {
                "required_mechanical_live": "blocked",
                "products": [
                    {
                        "product": "mechanical",
                        "reason": "Unable to connect to dns:///127.0.0.1:10000.",
                        "details": {"owned_process_cleanup": {"remaining": []}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    adapter = PyMechanicalSolverAdapter(capability)
    prepared = adapter.prepare(cae_ir, tmp_path / "work")
    precheck = adapter.precheck(prepared)
    result = adapter.solve(
        prepared,
        RunCallbacks(heartbeat=lambda: None, log=lambda _: None),
    )
    assert not precheck.valid
    assert [issue.code for issue in precheck.issues] == ["SOLVER_CAPABILITY_MISSING"]
    assert result.status is ExecutionStatus.BLOCKED_ENVIRONMENT
    assert result.converged is None
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_adapter_detects_source_hash_change_before_launch(tmp_path: Path) -> None:
    source = tmp_path / "box.step"
    source.write_bytes(b"original")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    graph = synthetic_graph(GeometrySpec(kind=GeometryKind.BOX)).model_copy(
        update={"source_path": str(source), "source_sha256": digest}
    )
    manifest = box_manifest().model_copy(
        update={"model": box_manifest().model.model_copy(update={"file": str(source)})}
    )
    resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
    cae_ir = compile_cae_ir(
        run_id="source-changed",
        blueprint=steady_blueprint(),
        manifest=manifest,
        recipe=steady_recipe(),
        graph=graph,
        resolution=resolution,
        mesh_policy=mesh_policy(),
    )
    adapter = PyMechanicalSolverAdapter(tmp_path / "missing-capability.json")
    prepared = adapter.prepare(cae_ir, tmp_path / "work")
    source.write_bytes(b"changed")
    report = adapter.precheck(prepared)
    assert {issue.code for issue in report.issues} == {
        "SOLVER_CAPABILITY_MISSING",
        "SOURCE_MODEL_MISMATCH",
    }
