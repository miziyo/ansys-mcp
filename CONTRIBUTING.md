# Contributing

Contributions are welcome when they remain inside the closed thermal-runner boundary.

## Public-content policy

Do not commit:

- official or third-party tutorials, notebooks, examples, documentation, or copied narrative;
- upstream example models, media, datasets, or product installation content;
- generated solver projects, meshes, results, logs, license data, or process snapshots;
- arbitrary script, command, executable, endpoint, or native solver-argument surfaces;
- content without a clear license and provenance record.

Small test assets must be project-owned, reproducibly generated from adjacent source, and documented.
Use source URLs and hashes only as metadata when external material is required at runtime; do not
mirror it in this repository.

## Required checks

```powershell
uv run python scripts/sanitize_tracked_paths.py
uv run python scripts/audit_public_repository.py --tree-only
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ansys_research_runner
uv run python scripts/export_schemas.py --check
uv run python scripts/export_mcp_tool_snapshot.py --check
uv run python -m pytest tests/unit tests/property tests/contract tests/integration tests/fault_injection -q
uv build
```

Live tests must be opt-in, bounded, use attempt-owned process cleanup, and leave generated material
only in ignored runtime directories.

By contributing, you agree that your project-owned contribution is provided under the repository's
MIT License. Do not submit code or assets that you do not have the right to license accordingly.
