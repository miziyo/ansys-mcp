# ADR 0006: SQLite WAL and exact-owned subprocess worker boundary

- Status: Accepted
- Date: 2026-08-23
- Gate: G6

## Context

Thermal solve calls can outlive an MCP request, crash native code, stop heartbeating, or leave
partial evidence. The controlling CLI/MCP process must remain inspectable, and cleanup must not
terminate an Ansys process that the user launched independently.

## Decision

Use the Python standard-library SQLite driver with `journal_mode=WAL`, `busy_timeout=5000`, and
foreign keys enabled. A worker claims one `QUEUED` row by a short `BEGIN IMMEDIATE` transaction and
conditional update. Every state edge is appended to `job_events` in the same transaction.

Run solver adapters in a child process. Record PID, parent PID, create time, executable path,
command-line SHA-256, launcher PID, discovery method, and run ID. Cleanup starts only from a record
whose PID and create time still match; descendants are then registered from that root and stopped
leaf-first. Process names alone never establish ownership.

Expired leases enter `RECOVERY_REQUIRED`. Pre-solve jobs may be explicitly requeued. Jobs that
reached `SOLVING`, `POSTPROCESSING`, or `EXPORTING` are not automatically replayed. Successful and
partial artifacts are hashed and retained regardless of worker exit status.

The G6 actual Mechanical lifecycle smoke is optional because G6 validates infrastructure rather
than thermal correctness. It must still run without a skip and prove cleanup. Its current
`BLOCKED_ENVIRONMENT` result does not become a false Mechanical support claim.

## Consequences

- Worker/native crashes do not terminate the CLI/MCP process.
- Status, failure, cancellation, and recovery decisions remain queryable after restart.
- SQLite writer contention is bounded and surfaced; it is not silently retried forever.
- Concurrency remains one in v0.x and distributed scheduling is not implied.
- Recovery after the solve boundary requires a human/application policy decision.
