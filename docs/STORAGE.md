# Storage layout

No drive letter, user profile, checkout directory, or Ansys installation path is fixed.

| Purpose | Default location |
| --- | --- |
| Repository | current checkout (`${REPOSITORY_ROOT}`) |
| Virtual environment | `${REPOSITORY_ROOT}/.venv` |
| UV cache | `${REPOSITORY_ROOT}/.cache/uv` |
| Runtime registry and reports | `${REPOSITORY_ROOT}/runtime` |
| Job-owned runs and artifacts | `${REPOSITORY_ROOT}/runtime/runs` |
| Temporary files | `${REPOSITORY_ROOT}/.tmp/runtime` |
| Python bytecode | `${REPOSITORY_ROOT}/.tmp/pycache` |

`ANSYS_RESEARCH_RUNTIME` can place mutable runtime data elsewhere. Maintenance scripts that need a
temporary work area use the operating-system temporary directory unless `ANSYS_RESEARCH_WORK_ROOT`
is set.

The Ansys installation is discovered from `ANSYS_RESEARCH_ANSYS_ROOT`, versioned `AWP_ROOT###`
variables, and Windows Program Files metadata. It may reside on any drive.

All mutable locations are ignored by Git and excluded from wheel and source-distribution content.
