# Core capabilities

The public package provides only the bounded thermal runner and its diagnostics. Product capability
reports are generated at runtime under `runtime/` and are not committed.

## Static discovery

`doctor` checks the host, writable runtime, dynamic Ansys installation root, and importability of:

- `ansys-common-mcp`
- `ansys-dpf-core`
- `ansys-geometry-core`
- `ansys-mechanical-core`
- `ansys-meshing-prime`
- `ansys-workbench-core`

Static discovery does not launch a product or prove license availability.

## Optional live probes

- `doctor --live` performs bounded Workbench and Mechanical checks.
- `geometry-doctor --live` checks the Geometry/SpaceClaim and Prime inspection boundary.
- `solver-doctor --live` checks the fixed Prime → MAPDL → DPF route.

Every child process is bounded and cleanup is limited to identities observed as descendants of the
current attempt.

## Activated execution surface

The production worker consumes only validated CAE-IR from the Job Registry. It supports the narrow
one-solid thermal envelope described in [supported-envelope.md](supported-envelope.md). It does not
accept arbitrary product commands, scripts, executables, endpoints, or native solver arguments.
