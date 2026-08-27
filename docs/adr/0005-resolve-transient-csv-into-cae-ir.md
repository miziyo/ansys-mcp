# ADR 0005: Resolve transient CSV profiles before the solver worker

- Status: accepted
- Date: 2026-08-23

## Context

G5 accepts time-series volumetric heat generation from a CSV with columns `time_s` and
`heat_generation_W_m3`. The architecture requires solver workers to consume only versioned
`ResolvedCAEIR`, not reopen public YAML or reinterpret mutable auxiliary inputs.

## Decision

- Parse CSV in the application service with a one-megabyte default limit and UTF-8 decoding.
- Require the exact two-column schema, finite values, 0 s start, strictly increasing unique time,
  and a final time equal to `analysis.end_time`.
- Resolve the source path beneath an allowed input root.
- Copy the exact CSV bytes into the Run Bundle request directory and hash them with SHA-256.
- Embed the source identity and normalized SI points in `ResolvedCAEIR.resolved_time_profiles`.
- Leave interpolation semantics to a reviewed solver adapter; do not infer or silently resample the
  input profile in the common core.

## Consequences

The worker receives immutable values and can prove which CSV produced them. A changed, malformed,
oversized, escaping, duplicate-time, or end-mismatched profile fails before solver launch. The
contract adds modest CAE-IR size but avoids a mutable external dependency during execution.
