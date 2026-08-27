# Supported envelope

The v0.x production backend is a fixed local Ansys Prime → MAPDL → DPF thermal route. The validated
generation is Ansys 2026 R1 with `ansys-meshing-prime` 0.10.4 and `ansys-dpf-core` 0.16.1.
Dynamic discovery can find later three-digit installation roots, but discovery alone does not establish
compatibility.

## Geometry

- STEP/STP, Parasolid, X_T/X_B, SCDOC/SCDOCX, DSCO, or PMDB accepted only when supported by the
  installed SpaceClaim and Prime versions.
- Exactly one solid body and one material domain.
- Explicit source length unit and globally aligned coordinate frame.
- Every graph identity is bound to the exact source SHA-256.
- Named Selection scope is preferred for supported native formats.
- Unnamed sources may use exact orientation, area, surface type, and global bounding-box extrema.
- Missing exact centroids, tied candidates, multi-solid ownership, or unsupported topology fail closed.

## Physics

- Linear isotropic thermal conductivity.
- Density and specific heat for transient analysis.
- Steady prescribed temperature, convection, and uniform volumetric heat generation.
- Transient initial temperature, fixed time step, convection, and uniform or bounded time-series
  volumetric heat generation.
- Global extrema, body average, hotspot, coordinate probes, and HDF5 temperature fields.

## Job and MCP boundary

- Local SQLite WAL registry with append-only events and atomic claim.
- Concurrency-one external worker.
- Launch, heartbeat, and wall-clock timeouts.
- Exact PID/create-time process ownership and descendant-only cleanup.
- Ten bounded local-STDIO MCP tools.
- No arbitrary Python, APDL, shell, journal, Workbench script, executable path, endpoint, or solver
  switch input.

## Explicitly unsupported

- Multiple solids, shells, beams, contact, radiation, fluids, CHT, or coupled structural physics.
- Temperature-dependent or anisotropic material models.
- Adaptive or anisotropic meshing.
- Distributed workers and remote HTTP MCP.
- Automatic changes to physics, mesh, or solver settings to avoid product or license limits.
- Any official or third-party example execution surface.

The generated box and cylinder test assets exercise contracts; they are not evidence for every valid
CAD shape or installed product configuration.
