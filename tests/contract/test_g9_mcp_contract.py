"""G9 in-process MCP tools, schemas, and bounded-response contracts."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from ansys_research_runner.mcp_server import AnsysResearchRunnerMCP
from tests.g8_fixtures import build_g8_workspace

_TOOL_NAMES = {
    "doctor",
    "inspect_model",
    "resolve_regions",
    "validate_run",
    "plan_run",
    "start_run",
    "get_run_status",
    "cancel_run",
    "get_run_summary",
    "list_run_artifacts",
}


@dataclass
class _FakeDispatcher:
    calls: int = 0

    def dispatch(self) -> None:
        self.calls += 1


def test_mcp_tool_schema_matches_committed_snapshot(tmp_path: Path) -> None:
    service, _, _ = build_g8_workspace(tmp_path)
    server = AnsysResearchRunnerMCP(application=service, dispatcher=_FakeDispatcher())

    async def collect() -> dict[str, object]:
        tools = await server.list_tools()
        return {
            "schema_version": 1,
            "tools": [
                {"name": tool.name, "input_schema": tool.parameters}
                for tool in sorted(tools, key=lambda item: item.name)
            ],
        }

    observed = collect_result = asyncio.run(collect())
    snapshot = json.loads(
        (Path(__file__).parents[2] / "docs" / "mcp-tools.v1.snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    assert {item["name"] for item in collect_result["tools"]} == _TOOL_NAMES  # type: ignore[index]
    assert observed == snapshot


def test_start_poll_cancel_flow_is_nonblocking_and_omits_job_request(tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    dispatcher = _FakeDispatcher()
    server = AnsysResearchRunnerMCP(application=service, dispatcher=dispatcher)

    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        async with Client(server) as client:
            started_at = time.monotonic()
            started = (
                await client.call_tool(
                    "start_run",
                    {"recipe_path": str(recipe_path), "run_id": "mcp-flow"},
                )
            ).data
            assert time.monotonic() - started_at < 1.0
            status = (await client.call_tool("get_run_status", {"run_id": "mcp-flow"})).data
            cancelled = (await client.call_tool("cancel_run", {"run_id": "mcp-flow"})).data
        return started, status, cancelled

    started, status, cancelled = asyncio.run(scenario())

    assert started["data"] == {
        "schema_version": 1,
        "run_id": "mcp-flow",
        "status": "QUEUED",
    }
    assert dispatcher.calls == 1
    assert status["data"]["job"]["status"] == "QUEUED"  # type: ignore[index]
    assert "request" not in status["data"]["job"]  # type: ignore[operator,index]
    assert cancelled["data"]["status"] == "CANCELLED"  # type: ignore[index]


def test_summary_response_never_embeds_large_temperature_field(tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    server = AnsysResearchRunnerMCP(application=service, dispatcher=_FakeDispatcher())
    service.run(recipe_path, run_id="bounded-summary")
    result_dir = service.paths.runs / "bounded-summary" / "results"
    result_dir.mkdir(parents=True)
    (result_dir / "summary.json").write_text(
        '{"schema_version":1,"temperature":{"minimum_K":293.15,"maximum_K":333.15}}',
        encoding="utf-8",
    )
    field = service.paths.runs / "bounded-summary" / "artifacts" / "temperature-field.h5"
    field.parent.mkdir(parents=True)
    field.write_bytes(b"x" * 2_000_000)

    async def scenario() -> dict[str, object]:
        async with Client(server) as client:
            return (await client.call_tool("get_run_summary", {"run_id": "bounded-summary"})).data

    response = asyncio.run(scenario())
    serialized = json.dumps(response)
    assert len(serialized) < 20_000
    assert "temperature-field.h5" not in serialized
    assert "fields" not in serialized


def test_local_stdio_server_startup_smoke() -> None:
    root = Path(__file__).parents[2]
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "ansys_research_runner.mcp_server", "--transport", "stdio"],
        cwd=str(root),
    )

    async def scenario() -> set[str]:
        async with Client(transport) as client:
            return {tool.name for tool in await client.list_tools()}

    assert asyncio.run(scenario()) == _TOOL_NAMES
