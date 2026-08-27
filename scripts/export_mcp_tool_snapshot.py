"""Export the deterministic unified MCP tool schema snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ansys_research_runner.mcp_server import AnsysResearchRunnerMCP


async def _collect() -> dict[str, object]:
    server = AnsysResearchRunnerMCP()
    tools = await server.list_tools()
    return {
        "schema_version": 1,
        "tools": [
            {"name": tool.name, "input_schema": tool.parameters}
            for tool in sorted(tools, key=lambda item: item.name)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / "docs" / "mcp-tools.v1.snapshot.json"
    rendered = json.dumps(asyncio.run(_collect()), indent=2, sort_keys=True) + "\n"
    if arguments.check:
        if not destination.is_file() or destination.read_text(encoding="utf-8") != rendered:
            print(f"MCP tool snapshot differs: {destination}")
            return 1
        print(f"verified MCP tool snapshot: {destination}")
        return 0
    destination.write_text(rendered, encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
