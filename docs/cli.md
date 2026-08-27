# Command-line interface

The CLI is a thin Typer facade over `ResearchRunnerApplication`. Long-running product work is
represented by a durable job and is never awaited by `run`.

## Commands

```text
ansys-research version
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

Every command supports human-readable output and `--json` where applicable.

## Input policy

Public paths and recipe references are confined to `ANSYS_RESEARCH_ROOT` by default. The policy
rejects:

- absolute paths outside the root;
- literal parent traversal;
- links or junctions that escape the root;
- unsupported extensions;
- YAML larger than 1 MiB, unsafe tags, and non-UTF-8 input;
- invalid units and selectors outside the closed data-only DSL;
- script, Python, code, command, shell, `eval`, or `exec` fields.

Production model extensions are `.step`, `.stp`, `.x_t`, `.x_b`, `.parasolid`, `.scdoc`, `.scdocx`,
`.dsco`, and `.pmdb`. Unsupported selectors and ambiguous candidates fail closed.

## Job semantics

`run` resolves and validates all input, compiles immutable CAE-IR, inserts a `QUEUED` record, and
returns. It does not claim solver success. `status`, `results`, and `artifacts` are read-only.
`cancel` requests a state-machine cancellation. `recover` requeues only an expired pre-solve job;
work that crossed `SOLVING` remains `RECOVERY_REQUIRED` for manual review.

For CLI-submitted work, start the queue drainer separately:

```powershell
uv run ansys-research-worker
```

The worker consumes only CAE-IR already stored in the registry and does not reopen public YAML or
invoke a shell.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 2 | Path, YAML, contract, selector, unit, or argument failure |
| 3 | Required live capability unavailable |
| 4 | Job not found, duplicate job ID, or invalid state |
| 10 | Unexpected internal failure |
