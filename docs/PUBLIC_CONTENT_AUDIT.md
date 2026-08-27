# Public content audit

## Distribution rule

The public repository contains only project-owned source, contracts, schemas, generated test
geometry, documentation, and tests needed to build and validate the core thermal runner.

It excludes all official or third-party tutorials, galleries, inventories, qualification records,
example datasets, copied product models, installed-product documentation, solver output, logs,
license data, and raw process snapshots.

## Asset provenance

The only tracked CAD assets are the box and cylinder STEP files under
`src/ansys_research_runner/resources/geometry/`. They are generated from the adjacent project-owned Python sources; exact
dimensions and expected topology are documented in the same directory.

No upstream binary fixture or product-generated CDB, project, mesh, or result file is tracked.

## License boundary

Project-owned material is released under MIT. Runtime dependencies are referenced by package name
and version constraints but are not vendored or relicensed. Product names identify separately
installed compatible software only.

## Automated enforcement

`scripts/audit_public_repository.py` fails on:

- excluded/generated path families;
- file names associated with excluded tutorial material;
- common solver/project output extensions;
- personal email addresses, user-profile paths, hostnames, process environments, and credential
  patterns;
- commit metadata that does not use the approved GitHub noreply identity.

`tests/unit/test_public_release.py` additionally asserts that no excluded path or solver-output
format is tracked. CI reruns both checks, schema drift checks, the MCP tool snapshot, lint, typing,
tests, and package builds.
