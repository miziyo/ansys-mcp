# Architecture

The runner uses inward-only dependencies:

```text
domain <- services <- adapters <- runtime / CLI / MCP
```

Domain contracts contain no PyAnsys imports. YAML is compiled once into a versioned
`ResolvedCAEIR`; solver workers consume only that intermediate representation. Long-running
Ansys work occurs in an owned worker process, while the CLI and MCP facade submit and inspect
jobs through the application service.

## G2 contract flow

```text
Analysis Blueprint + Model Manifest + Run Recipe
                         |
GeometryAdapter -> GeometryGraph
                         |
restricted Selector AST -> RegionResolution
                         |
cross-contract validation + versioned mesh policy
                         |
ResolvedCAEIR -> solver worker (G4+)
```

Blueprint, Manifest, and Recipe reject unknown fields and cannot carry run-local body or face
IDs. The Geometry Graph may contain adapter-owned runtime IDs. They enter `ResolvedCAEIR` only
after resolution and are paired with the semantic role, selector, geometry fingerprint, stable
key, centroid, measure, surface classification, and parent body evidence.

The selector evaluator has no expression evaluator or Python callback. It walks a closed
Pydantic AST, sorts stable keys before resolution, retains ties, and applies cardinality only
after predicate evaluation. An exact-one tie is therefore an error, never an arbitrary choice.

## G3 official adapter boundary

Official product objects never cross into the domain layer. Each Geometry or Prime call executes in
an isolated child process and emits JSON observations. The application service evaluates those
observations against one complete minimum-capability set before an adapter may construct a
`GeometryGraph`.

```text
Discovery / SpaceClaim / Prime / Mechanical fallback
                         |
              isolated live observations
                         |
             Geometry capability evaluator
                  |                    |
       complete contract          missing property
                  |                    |
         GeometryGraph mapper     structured refusal
```

On the current Ansys 26.1 environment every official candidate reaches the refusal branch. Partial
counts and measures remain evidence, but they do not activate the adapter. In particular, a
bounding-box center or representative surface point is not promoted to an exact centroid.

## G4 steady-thermal boundary

The application service compiles both supported steady reference cases through the same Blueprint,
selector, compiler, solver-adapter, verifier, and Run Bundle path:

```text
Blueprint + Manifest + Recipe + GeometryGraph
                    |
       RegionResolution -> ResolvedCAEIR
                    |
          SolverAdapter protocol
           |                  |
 Prime -> MAPDL -> DPF     test-only fake
  official batch route    contract evidence
           |                  |
           +------ ThermalObservation
                         |
       independent analytic/conservation verifier
                         |
        multidimensional result quality + Run Bundle
```

The production adapter stages only `ResolvedCAEIR`, verifies the source CAD hash, imports and meshes
the original CAD through release-matched Ansys Prime, writes MAPDL CDB/APDL input, executes the
installed MAPDL batch binary, and extracts mesh/temperature fields through DPF. Prime and MAPDL run
as exact tracked child process trees. The fake adapter exists only in tests and cannot be selected
by production. Execution success, solver convergence, mesh/time-step verification, energy balance,
analytic reference, experimental validation, and provenance remain separate fields.

## G5 transient extension

Transient runs reuse the G2 selector/compiler, G4 SolverAdapter, HDF5 writer, quality model, and Run
Bundle service. Transient-only application code resolves time controls and bounded CSV profiles:

```text
RunRecipe + profile.csv
        |
strict CSV parser: exact header, size, UTF-8, finite values,
strictly increasing time, 0 s start, requested end-time match
        |
ResolvedHeatGenerationProfile (source path + SHA-256 + SI points)
        |
ResolvedCAEIR -> existing SolverAdapter / Run Bundle core
```

The worker never reopens the public Recipe or interprets arbitrary CSV columns. Profile bytes are
copied into `request/`, their hash is included in `input_hashes.json`, and the normalized points are
embedded in CAE-IR. The lumped verifier independently derives `hA/(rho cp V)` and checks the Biot
limit before comparing every frame, maximum absolute error, and RMSE.

## G6 durable job boundary

Long-running work is claimed from a SQLite WAL registry and executed by one reviewed subprocess
worker at a time. The CLI/MCP process never imports solver code into its own process.

```text
caller -> jobs.sqlite (QUEUED + audit event)
              |
       atomic conditional claim
              |
      single subprocess worker
       | heartbeat/events |
       +---- supervisor --+
              |
   artifact hashes + terminal state
```

Every state edge and recovery decision is appended to `job_events` in the same short transaction
as the current-state update. A stale pre-solve lease may be requeued after passing through
`RECOVERY_REQUIRED`; work that reached `SOLVING` is never automatically replayed. Process ownership
requires PID and create time plus executable/command provenance. Cleanup discovers descendants only
from matching owned roots and stops them leaf-first; process names and installation-wide scans never
grant ownership.

## G7 optional Workbench coupling boundary

Workbench remains an optional backend capability, not a dependency of direct thermal execution.
Two isolated probes separate deterministic project lifecycle from the Mechanical handoff:

```text
probe A: launch -> reviewed create/save -> reopen/list -> archive -> normal exit
probe B: launch -> same fixed project -> start Mechanical server -> PyMechanical -> shutdown
```

This split preserves proven project and archive evidence even if product-managed Mechanical startup
or shutdown hangs. Each Workbench launcher writes PID, create time, command-line fingerprint, and its
unique server workdir before any long call. The parent validates all four before cleaning a detached
Workbench tree. A Workbench-generated licensing cleanup helper is eligible only when its ACL token
matches a token captured from that exact owned tree.

## G8 application and CLI boundary

The CLI performs formatting and exit-code mapping only. Every operation crosses one application
service boundary:

```text
CLI -> ResearchRunnerApplication
          |-- confined path + bounded contract loading
          |-- GeometryAdapter -> resolve -> validate -> compile CAE-IR
          `-- SQLite JobRegistry
```

`run` validates and compiles before inserting a `QUEUED` record. It does not start or own an Ansys
session and does not convert queue acceptance into solver success. The job request contains the
resolved CAE-IR, so a worker never reinterprets arbitrary YAML or code. `ansys-research-worker`
drains queued CLI work; the MCP facade starts the same detached queue drainer after submission. The
production application constructs the fail-closed official Geometry adapter; the synthetic or
committed-fixture adapter is injected only by tests and has no production selector.

All machine output is a `cli-response@1` envelope whose data branch is a closed union of typed
command results. Path confinement is checked lexically before canonical resolution so literal
parent traversal, root escape, and link/junction escape remain distinguishable error codes.

## G9 local MCP boundary

`AnsysResearchRunnerMCP` extends `ansys-common-mcp.PyAnsysBaseMCP` with `need_python=False`.
`ThermalResearchRunnerMCP` remains an import alias for compatibility. It exposes ten closed
application operations and deliberately omits the common arbitrary-code tool.

```text
local STDIO client -> thin MCP tool -> ResearchRunnerApplication
                                        |
                                  jobs.sqlite: QUEUED
                                        |
                           detached concurrency-one drainer
                                        |
                            supervised thermal_worker process
                                        |
                                Prime -> MAPDL -> DPF
```

The MCP process owns no interactive product session. `start_run` returns only the run ID and queued
status, while status, summary, and artifact tools return bounded metadata. Field arrays remain in
HDF5. HTTP transport is not accepted in v0.x.

Pi has no built-in MCP client. The optional project-owned Pi extension uses the official TypeScript
MCP SDK to launch the same fixed STDIO command, verifies the exact ten-tool set before use, confines
inputs to the current Pi project, and closes the child transport on session shutdown.

## G10 mesh verification

A mesh study clones one validated Recipe into coarse, balanced, and fine child runs. Each child has
its own run ID and Run Bundle. The raw child observations are persisted separately from aggregation,
so aggregation can be rerun without a license or solver launch.

```text
Recipe practical tolerance
          |
  coarse  balanced  fine   (independent bundles)
          |
element count + runtime + target metric + execution/physics status
          |
balanced-to-fine difference -> mesh verification status
```

Child execution, child physical verification, aggregate execution, and mesh convergence are never
collapsed into one boolean. A failed child produces `PARTIAL / INCONCLUSIVE` while preserving all
successful siblings.
