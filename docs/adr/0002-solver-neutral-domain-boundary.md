# ADR 0002: Solver-neutral domain boundary

## Status

Accepted for v0.x on 2026-08-23.

## Decision

The `ansys_research_runner.domain` package imports no PyAnsys product package. Official APIs are
isolated behind adapters. Public YAML is parsed and validated once, then compiled into a
versioned `ResolvedCAEIR`; solver workers do not reinterpret the original YAML.

## Consequences

Core contracts, selectors, units, and compilation remain testable when a Mechanical license or
native startup is unavailable. Live product capability remains explicit and cannot be replaced
by synthetic success.
