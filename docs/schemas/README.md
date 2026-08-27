# Versioned contract schemas

These JSON Schemas are generated from the authoritative Pydantic v1 contract models. Do not edit
the JSON files manually.

```powershell
.venv\Scripts\python.exe scripts\export_schemas.py
.venv\Scripts\python.exe scripts\export_schemas.py --check
```

`analysis-blueprint`, `model-manifest`, and `run-recipe` are public inputs and contain no
run-local Ansys entity IDs. `geometry-graph` is adapter-internal. `resolved-cae-ir` is the only
solver-bound contract that may contain such IDs, and every selected ID has resolution evidence.

G4 adds `scalar-result-summary`, `thermal-verification`, `result-quality`, `run-bundle-state`, and
`run-bundle-manifest`. These keep solver execution, numerical checks, physical checks, and provenance
independent instead of reducing a result to one PASS value.

G5 adds `transient-profile` and `transient-observation`; the updated Run Recipe and Resolved CAE-IR
schemas carry a closed time-profile load reference and its immutable resolved data respectively.

G6 adds `job-record`, `job-event`, `owned-process`, `job-artifact`, and `resource-snapshot`. These
contracts keep durable state, append-only transitions, exact process identity, artifact integrity,
and resource observations independently inspectable.

G7 adds `workbench-coupling-capability`, which separates project lifecycle, archive integrity,
Mechanical server start, PyMechanical handoff, coupling-probe completion, and cleanup evidence.

G8 adds `cli-response`, the versioned success/failure envelope and closed union of typed command
results used by every public CLI command with `--json`.

G10 adds `mesh-study-result`; the updated Run Recipe declares explicit coarse, balanced, and fine
profiles plus either a relative or absolute convergence tolerance. Child execution and physical
verification remain separate from the final convergence decision.
