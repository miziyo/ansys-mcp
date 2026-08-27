# ADR 0009: Thin STDIO MCP over the external Registry worker

## Status

Accepted on 2026-08-23.

## Context

MCP tool calls must remain bounded, must not embed large fields, and must survive solver-worker
failure. `ansys-common-mcp` also offers persistent Python execution helpers that are intentionally
outside this runner's security envelope.

## Decision

Extend `PyAnsysBaseMCP` with `need_python=False`, expose only the ten specified application tools,
and support local STDIO only. `start_run` validates and enqueues through the application service,
returns `run_id/QUEUED`, and starts the external concurrency-one queue drainer. The MCP server never
owns an interactive Ansys session. Summary and artifact tools return bounded JSON and metadata only.

## Consequences

- MCP and CLI use the same application, Registry, worker, state machine, and artifact contracts.
- Arbitrary Python, shell, APDL, and Workbench script tools are absent.
- A solver crash does not terminate the MCP process, and terminal state remains pollable.
- HTTP transport is rejected in v0.x.
