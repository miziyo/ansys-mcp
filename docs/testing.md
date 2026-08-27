# Testing

The default suite is solver-free and does not require Ansys or a license:

```powershell
uv run python -m pytest tests/unit tests/property tests/contract tests/integration tests/fault_injection -q
```

It covers:

- strict units and thermal contracts;
- selector cardinality, ambiguity, unsupported capability, and dependency handling;
- deterministic Geometry Graph and CAE-IR serialization;
- path confinement, YAML bounds, and arbitrary-script-field rejection;
- SQLite WAL job transitions, leases, cancellation, recovery, and process ownership;
- steady and transient physical-verification logic;
- CLI and ten-tool MCP schemas, bounded responses, and real local STDIO startup;
- public-tree privacy and content-policy enforcement.

Static gates:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ansys_research_runner
uv run python scripts/export_schemas.py --check
uv run python scripts/export_mcp_tool_snapshot.py --check
uv run python scripts/audit_public_repository.py --tree-only
uv build
```

Tests under `tests/live/` are opt-in, require a compatible installed product and license, and are not
part of CI. Their generated projects, meshes, results, databases, logs, and reports remain under
ignored runtime locations.

The STEP fixtures under `src/ansys_research_runner/resources/geometry/` are generated from adjacent project-owned Python
source. Exact dimensions and topology are documented in that directory.
