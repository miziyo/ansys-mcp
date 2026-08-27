# ADR 0008: Prime, MAPDL batch, and DPF as the production thermal route

## Status

Accepted on 2026-08-23.

## Context

The original direct PyMechanical routes could not launch reliably in the installed Ansys 26.1
environment. Remote gRPC failed to connect, embedding failed during OpenSSL provider initialization,
and a verified Workbench-managed Mechanical connection did not produce or export a solved thermal
model through the attempted automation path. The installed official MAPDL executable, release-matched
Prime service, and DPF reader were independently available.

## Decision

Use the original CAD as input to official Ansys Prime, export a tetrahedral MAPDL CDB, compile a
closed data-driven APDL thermal deck from resolved CAE-IR, execute installed MAPDL in batch mode, and
extract scalar/mesh/field results with DPF. Keep Workbench as an optional coupling capability and
retain the fail-closed PyMechanical adapter for diagnostics.

Every licensed executable remains in an isolated, tracked process tree. The worker accepts no user
Python, APDL, shell, or Workbench script. Physical reference checks remain outside the solver.

## Consequences

- G4 and G5 can be qualified with actual official Ansys results despite standalone Mechanical
  startup failures.
- MAPDL v0 support is narrower than the complete Blueprint language; unsupported combinations fail
  precheck.
- G3 is not bypassed. The committed live corpus uses independent descriptors tied to exact CAD hashes,
  while arbitrary production CAD remains blocked until an official Geometry Graph mapper exists.
- DPF becomes the common source for the versioned HDF5 field contract and coordinate probes.
