# ADR 0001: Semantic selectors instead of persisted runtime IDs

## Status

Accepted for v0.x on 2026-08-23.

## Decision

Blueprints, Model Manifests, Run Recipes, and stable public identifiers cannot contain Ansys
body, face, or node runtime IDs. Model regions are described by a closed semantic Selector DSL.
The resolver keeps tied candidates and enforces declared cardinality without arbitrary choice.

Run-local IDs may appear in `ResolvedCAEIR` only with selection evidence and the inspection-time
geometry fingerprint. This permits a solver adapter to address entities while making the mapping
auditable and preventing a previous run's transient IDs from becoming user configuration.

## Consequences

Symmetric or under-described geometry can require a more explicit Model Manifest and can fail as
ambiguous. That failure is intentional: the runner favors a reviewable refusal over silently
loading or constraining the wrong face.
