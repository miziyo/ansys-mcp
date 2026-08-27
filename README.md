# ansys-mcp

A local, closed-surface MCP and command-line runner for bounded thermal workflows on an installed
Ansys system. The public repository contains project-owned source, schemas, generated test geometry,
and tests only. It does **not** redistribute Ansys or PyAnsys tutorials, example datasets, product
files, documentation, solver output, or qualification archives.

This is an independent project and is not an official Ansys product.

## Supported core

The current implementation was validated against the following local generation. Discovery is
version-dynamic, but a newly discovered release must be qualified before it is treated as supported.

| Component | Validated version | Purpose |
| --- | --- | --- |
| Ansys Student | 2026 R1 (`261`) | Local product installation |
| `ansys-common-mcp` | `0.3.3` | MCP base |
| `ansys-geometry-core` | `0.17.1` | Confined CAD inspection |
| `ansys-meshing-prime` | `0.10.4` | Thermal mesh generation |
| MAPDL | 2026 R1 | Batch thermal solve |
| `ansys-dpf-core` | `0.16.1` | Result extraction |
| `ansys-workbench-core` | `0.14.0` | Optional lifecycle capability |
| `ansys-mechanical-core` | `0.13.2` | Optional capability probe |
| Python | `3.12` | Runtime |

Fluent, CFX, ACP, optiSLang, System Coupling, AEDT, EDB, LS-DYNA, Twin Runtime, Rocky, Speos,
EnSight, TurboGrid, and Dynamic Reporting are not public execution surfaces in this repository.
Previous compatibility research for those products is not distributed here.

## What the runner does

- discovers a standard Ansys installation without a fixed drive, profile, or checkout path;
- confines model and recipe inputs to a configured root;
- validates strict Pydantic/YAML contracts with explicit units;
- resolves regions through a closed semantic selector AST;
- compiles immutable solver-neutral CAE-IR;
- enqueues work in a local SQLite WAL registry;
- executes a fixed Prime → MAPDL → DPF thermal worker;
- records bounded summaries and artifact hashes while keeping field arrays out of MCP responses;
- owns and cleans only process trees identified by PID and creation time.

The activated v0.x physics envelope is intentionally narrow: one solid, isotropic thermal material,
steady or transient conduction, prescribed temperature, convection, and uniform or bounded
time-series volumetric heat generation. Unsupported geometry, selectors, physics, and lifecycle
states fail closed.

## MCP tools

The local STDIO server exposes ten tools:

- `doctor`
- `inspect_model`
- `resolve_regions`
- `validate_run`
- `plan_run`
- `start_run`
- `get_run_status`
- `cancel_run`
- `get_run_summary`
- `list_run_artifacts`

There is no tutorial catalog or tutorial runner. No tool accepts Python, APDL, Scheme, journals,
Workbench scripts, shell commands, executable paths, RPC endpoints, or caller-selected solver
switches.

## Installation

```powershell
git clone https://github.com/miziyo/ansys-mcp.git
cd ansys-mcp
uv sync --frozen
uv run ansys-research doctor --json
```

Standard installations are discovered automatically. A nonstandard installation can be selected
for the current process:

```powershell
$env:ANSYS_RESEARCH_ANSYS_ROOT = "<installation-root>"
```

No machine-level Ansys configuration is changed.

## MCP configuration

After installing the package or tool so that `ansys-research-mcp` is on `PATH`:

```json
{
  "command": "ansys-research-mcp",
  "args": ["--transport", "stdio"]
}
```

Only local STDIO is accepted.

## Pi integration

Pi intentionally has no built-in MCP client. This repository therefore includes a reviewed Pi
extension that bridges the same ten tools through the official MCP TypeScript SDK; it does not add
another product execution surface.

After the `v0.13.0` release is available:

```powershell
uv tool install "ansys-research-runner @ git+https://github.com/miziyo/ansys-mcp.git@v0.13.0" --python 3.12
pi install git:github.com/miziyo/ansys-mcp@v0.13.0
```

Restart Pi or run `/reload`, then use `/ansys-mcp-status`. The extension launches only the fixed
`ansys-research-mcp --transport stdio` command, verifies that the server exposes exactly the expected
ten tools, confines inputs to the current Pi project, and stores mutable MCP state under Pi's user
configuration directory.

## CLI

```text
ansys-research doctor
ansys-research geometry-doctor
ansys-research solver-doctor --live
ansys-research inspect <model>
ansys-research resolve <recipe>
ansys-research validate <recipe>
ansys-research plan <recipe> [--run-id ID]
ansys-research run <recipe> [--run-id ID]
ansys-research status <run_id>
ansys-research cancel <run_id>
ansys-research results <run_id>
ansys-research artifacts <run_id>
ansys-research recover
```

See [CLI documentation](docs/cli.md), [architecture](docs/architecture.md), and the
[supported envelope](docs/supported-envelope.md).

## Public-content boundary

The following are deliberately absent from the public repository and release artifacts:

- official or third-party tutorial source and notebooks;
- tutorial inventories, qualification matrices, and copied narrative;
- upstream example models, media, and datasets;
- installed-product Help content or sample projects;
- solver projects, meshes, results, logs, license data, and process snapshots;
- generated `runtime/`, `artifacts/`, `workspace/`, environment, and cache directories.

The STEP files under `src/ansys_research_runner/resources/geometry/` are generated from adjacent project-owned Python
sources with documented dimensions. They are not copied Ansys examples.

Run the publication gate before a release:

```powershell
uv run python scripts/sanitize_tracked_paths.py
uv run python scripts/audit_public_repository.py --tree-only
```

## Development

```powershell
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ansys_research_runner
uv run python -m pytest tests/unit tests/property tests/contract tests/integration tests/fault_injection -q
uv build
```

Live tests require an installed product and license and are not run by default.

## License and trademarks

Project-owned source is licensed under the [MIT License](LICENSE). Runtime dependencies are not
vendored and remain under their respective licenses. See [third-party notices](THIRD_PARTY_NOTICES.md).

Ansys and Ansys product names are trademarks or registered trademarks of Ansys, Inc. or its
affiliates. Their use here identifies compatible separately installed products only and does not
imply endorsement.
