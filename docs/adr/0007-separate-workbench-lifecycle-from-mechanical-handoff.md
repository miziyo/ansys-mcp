# ADR 0007: Separate Workbench lifecycle from Mechanical handoff

- Status: Accepted
- Date: 2026-08-23
- Gate: G7

## Context

PyWorkbench 0.14.0 can create and persist Workbench 261 projects even while Mechanical startup is
unavailable through other official paths. `LaunchMechanicalServerOnSystem` and the following
PyMechanical connection or shutdown can block much longer than project operations. Treating them as
one all-or-nothing process would discard valid lifecycle evidence and could leave a detached
Workbench process after the Python probe is timed out.

## Decision

Run two bounded actual-product probes. The first proves create/save/reopen/system-list/archive and
normal Workbench exit. The second alone attempts the Workbench-managed Mechanical server and
PyMechanical handoff. `workbench_coupling` is true only if the second probe connects and completes
normally; direct thermal behavior never depends on it.

The adapter persists launcher PID, create time, command-line SHA-256, and unique server workdir as
soon as Workbench starts. The parent requires an exact identity and workdir match before stopping a
detached launcher tree. License cleanup helpers are associated only through an ACL token captured
from that owned tree.

## Consequences

- Project/archive support can be reported independently and truthfully.
- A hanging handoff does not erase completed project evidence or affect direct thermal services.
- Handoff stage, port, connection liveness, normal completion, and cleanup remain separate fields.
- Arbitrary user Workbench scripts are still outside the public surface.
