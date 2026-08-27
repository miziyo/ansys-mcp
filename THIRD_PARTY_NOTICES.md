# Third-party notices

This repository does not vendor Ansys software, PyAnsys packages, official tutorials, example
datasets, product documentation, or product-generated files.

Runtime dependencies are downloaded from their own distribution channels and remain under their
own licenses. The locked release was reviewed against the following declared licenses:

| Direct dependency | License |
| --- | --- |
| `ansys-common-mcp` | Apache-2.0 |
| `ansys-dpf-core` | MIT |
| `ansys-geometry-core` | MIT |
| `ansys-mechanical-core` | MIT |
| `ansys-meshing-prime` | MIT |
| `ansys-workbench-core` | MIT |
| `fastmcp` | Apache-2.0 |
| `h5py` | BSD-3-Clause |
| `mcp` | MIT |
| `numpy` | BSD-3-Clause and bundled component licenses recorded by NumPy |
| `pint` | BSD |
| `platformdirs` | MIT |
| `portalocker` | BSD-3-Clause |
| `psutil` | BSD-3-Clause |
| `pydantic` | MIT |
| `PyYAML` | MIT |
| `typer` | MIT |

The optional Pi bridge uses the MIT-licensed `@modelcontextprotocol/sdk`,
`@earendil-works/pi-coding-agent`, and `typebox` packages. They are resolved by Pi/npm and are not
vendored.

This table is informational; the authoritative license text is the one distributed with each
installed dependency. No dependency source is copied into this repository or wheel.

The STEP test assets under `tests/assets/geometry/` are generated from adjacent project-owned Python
source and are covered by this project's MIT License. Manual regeneration uses the separately
installed Apache-2.0-licensed `build123d` package; neither that package nor its source is vendored.

Ansys and Ansys product names are trademarks or registered trademarks of Ansys, Inc. or its
affiliates. Names are used only to identify compatible, separately installed products. This
independent project is not endorsed by Ansys, Inc.
