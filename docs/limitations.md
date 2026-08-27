# Limitations

- The activated route supports one solid and a narrow thermal physics envelope only.
- The complete arbitrary-CAD semantic and topological-naming problem is not solved.
- Named Selection scope is preferred. Centroid selectors fail closed when the backend cannot provide
  exact centroids, and tied candidates remain ambiguous.
- MAPDL boundary scoping assumes globally aligned axes and supported planar faces.
- Solver convergence is not proof of physical validity. Execution, convergence, mesh verification,
  reference or conservation checks, and provenance remain separate statuses.
- Product and Python-client discovery does not prove API compatibility or license availability.
- Workbench and Mechanical checks are optional and are not part of the direct thermal solve route.
- Transient profiles accept only `time_s,heat_generation_W_m3`, must begin at zero, end at the
  requested time, and contain finite strictly increasing points.
- Job concurrency is one. A stale job that crossed the solve boundary is never replayed automatically.
- Cancellation is process-boundary safe but cannot guarantee an application-level graceful save.
- Only attempt-owned PID/create-time identities are eligible for fallback termination.
- The CLI `run` command enqueues only; `ansys-research-worker` drains CLI-submitted jobs. MCP
  `start_run` starts the same detached drainer automatically.
- Product-generated files and runtime capability evidence are machine-specific and untracked.
- No tutorial, example-gallery, or arbitrary product-command execution API is included.
