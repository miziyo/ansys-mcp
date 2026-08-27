# ADR 0011: Source-bound exact Geometry tier for Ansys 2026 R1

## Status

Accepted and live-qualified on 2026-08-24.

## Context

The full G3 contract requires exact body and face centroids plus metamorphic stable identity for
arbitrary CAD. The installed Ansys 2026 R1 Geometry API exposes exact volume, area, bounding boxes,
surface classes, and SpaceClaim face orientation, but exact face centroid support starts in a later
release. Treating a bounding-box midpoint as a centroid would silently change selector meaning.

## Decision

Add a distinct `source_bound_exact` capability tier without weakening G3:

- inspect one immutable solid from STEP/STP, Parasolid, SpaceClaim, Discovery, or PMDB sources;
- bind the graph and every stable signature to the exact source SHA-256;
- preserve exact volume, area, global AABB, surface type, and face normal/axis from SpaceClaim;
- preserve SCDOCX/PMDB Named Selection membership and expose `named_selection` selectors;
- expose `bounding_box_extreme` only in source CAD global axes;
- return `UNSUPPORTED_SELECTOR_CAPABILITY` when a requested selector needs an absent centroid;
- return `AMBIGUOUS` for tied candidates instead of choosing by a runtime entity ID;
- compile the inspected absolute source path and hash into CAE-IR so a worker cannot reinterpret a
  relative path from another working directory.

The full `full_semantic` G3 contract remains blocked and separately reported. The narrower tier is
qualified as G14 and matches the production MAPDL v0 envelope: one solid, CAD-global axes, and
global-axis planar temperature boundaries.

## Evidence

The G14 live test generates and reopens an SCDOCX box with `THERMAL_DOMAIN`, `COLD_FACE`,
`HOT_FACE`, and `EXTERIOR` Named Selections, inspects both SCDOCX and STEP twice, verifies repeatable
fingerprints and stable-key sets, verifies centroid-dependent failure, and completes an actual
SpaceClaim → Prime → MAPDL → DPF thermal job.
