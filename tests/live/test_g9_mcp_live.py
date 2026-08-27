"""Actual MCP-to-registry-to-Prime/MAPDL/DPF end-to-end flow."""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path

import pytest
import yaml
from fastmcp import Client

from ansys_research_runner.adapters.geometry.base import (
    ArtifactRecord,
    GeometryCapabilityReport,
    GeometryInspectionRequest,
)
from ansys_research_runner.adapters.geometry.base import TestGeometryKind as GeometryKind
from ansys_research_runner.adapters.geometry.base import TestGeometrySpec as GeometrySpec
from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.selectors import RegionResolution
from ansys_research_runner.io import atomic_write_json, atomic_write_text
from ansys_research_runner.mcp_server import ThermalResearchRunnerMCP
from ansys_research_runner.services.application_service import ResearchRunnerApplication
from ansys_research_runner.services.live_thermal_gate_service import (
    _box_manifest,
    _source_graph,
    _steady_box_recipe,
)
from ansys_research_runner.services.production_worker_service import BackgroundWorkerDispatcher


class _CommittedBoxGeometryAdapter:
    """Test-corpus adapter returning the independently defined committed box graph."""

    def __init__(self, source: Path, graph: GeometryGraph) -> None:
        self._source = source.resolve()
        self._graph = graph

    def probe_capabilities(self) -> GeometryCapabilityReport:
        return GeometryCapabilityReport(
            backend="committed_g9_fixture",
            available=True,
            capabilities=("geometry_graph",),
        )

    def inspect(self, request: GeometryInspectionRequest) -> GeometryGraph:
        if request.model_path.resolve() != self._source:
            raise ValueError("G9 live fixture adapter received an unexpected model.")
        return self._graph

    def generate_test_asset(self, spec: GeometrySpec, output_dir: Path) -> Path:
        del spec, output_dir
        raise ValueError("The committed G9 adapter is inspection-only.")

    def create_selection_preview(
        self,
        graph: GeometryGraph,
        resolution: RegionResolution,
        output_dir: Path,
    ) -> list[ArtifactRecord]:
        del graph, resolution, output_dir
        return []

    def close(self) -> None:
        return None


@pytest.mark.ansys_live
def test_actual_mcp_background_job_reaches_summary_and_artifacts() -> None:
    paths = RunnerPaths.from_environment()
    inputs = paths.runtime / "gate-inputs" / "G9"
    inputs.mkdir(parents=True, exist_ok=True)
    source = inputs / "g3_box.step"
    shutil.copy2(resource_path("geometry", "g3_box.step"), source)
    manifest_path = inputs / "box.manifest.yaml"
    recipe_path = inputs / "box.recipe.yaml"
    manifest = _box_manifest(source)
    recipe = _steady_box_recipe(manifest_path)
    atomic_write_text(
        manifest_path,
        yaml.safe_dump(
            manifest.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            allow_unicode=True,
        ),
    )
    atomic_write_text(
        recipe_path,
        yaml.safe_dump(
            recipe.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            allow_unicode=True,
        ),
    )
    run_id = f"g9-live-{uuid.uuid4().hex[:12]}"
    application = ResearchRunnerApplication(
        paths=paths,
        allowed_input_root=paths.root,
        geometry_adapter_factory=lambda: _CommittedBoxGeometryAdapter(
            source,
            _source_graph(GeometryKind.BOX, source),
        ),
    )
    server = ThermalResearchRunnerMCP(
        application=application,
        dispatcher=BackgroundWorkerDispatcher(paths),
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        async with Client(server) as client:
            submitted_at = time.monotonic()
            submitted = (
                await client.call_tool(
                    "start_run",
                    {"recipe_path": str(recipe_path), "run_id": run_id},
                )
            ).data
            assert time.monotonic() - submitted_at < 20.0
            deadline = time.monotonic() + 300.0
            while True:
                status = (await client.call_tool("get_run_status", {"run_id": run_id})).data
                current = status["data"]["job"]["status"]
                if current in {
                    "SUCCEEDED",
                    "FAILED_INPUT",
                    "FAILED_LAUNCH",
                    "FAILED_LICENSE",
                    "FAILED_PRECHECK",
                    "FAILED_SOLVER",
                    "FAILED_RESOURCE",
                    "FAILED_POSTPROCESS",
                    "FAILED_EXPORT",
                    "CANCELLED",
                }:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Timed out polling MCP run {run_id}: {current}")
                await asyncio.sleep(0.25)
            summary = (await client.call_tool("get_run_summary", {"run_id": run_id})).data
            artifacts = (await client.call_tool("list_run_artifacts", {"run_id": run_id})).data
        return submitted, summary, artifacts

    submitted, summary, artifacts = asyncio.run(scenario())

    assert submitted["data"]["status"] == "QUEUED"
    assert summary["data"]["status"] == "SUCCEEDED"
    assert summary["data"]["summary"]["temperature"]["maximum_K"] == pytest.approx(  # type: ignore[index]
        373.15
    )
    artifact_names = {item["path"] for item in artifacts["data"]["artifacts"]}  # type: ignore[index]
    assert "artifacts/temperature-field.h5" in artifact_names
    assert "artifacts/summary.json" in artifact_names
    owned = application.registry.list_owned_processes(run_id)
    assert owned
    assert all(record.ended_at is not None for record in owned)
    atomic_write_json(
        paths.runtime / "g9_mcp_live_report.json",
        {
            "schema_version": 1,
            "gate": "G9",
            "status": "PASSED",
            "run_id": run_id,
            "submission_status": submitted["data"]["status"],
            "terminal_status": summary["data"]["status"],
            "artifact_paths": sorted(artifact_names),
            "owned_process_cleanup": {
                "remaining": [record.pid for record in owned if record.ended_at is None]
            },
        },
    )
