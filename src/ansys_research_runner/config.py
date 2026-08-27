"""Runtime configuration and project-owned path policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path


def repository_root() -> Path:
    """Return the current project root used for confined public inputs."""

    return Path.cwd().resolve()


def resource_path(*parts: str) -> Path:
    """Return one immutable resource shipped inside the Python package."""

    return Path(__file__).resolve().parent.joinpath("resources", *parts)


@dataclass(frozen=True, slots=True)
class RunnerPaths:
    """Resolved project-owned paths used by the runner."""

    root: Path
    runtime: Path
    runs: Path
    gates: Path
    blockers: Path
    database: Path

    @classmethod
    def from_environment(cls) -> RunnerPaths:
        """Build paths from the environment, defaulting to the current project."""

        configured_root = os.getenv("ANSYS_RESEARCH_ROOT")
        root = (
            Path(configured_root).expanduser().resolve() if configured_root else repository_root()
        )
        configured_runtime = os.getenv("ANSYS_RESEARCH_RUNTIME")
        runtime = (
            Path(configured_runtime).expanduser().resolve()
            if configured_runtime
            else (root / "runtime").resolve()
        )
        return cls(
            root=root,
            runtime=runtime,
            runs=runtime / "runs",
            gates=runtime / "gates",
            blockers=runtime / "blockers",
            database=runtime / "jobs.sqlite",
        )

    def ensure_runtime(self) -> None:
        """Create only runtime directories required by the current application."""

        for path in (self.runtime, self.runs, self.gates, self.blockers):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def fallback_cache(self) -> Path:
        """Return an OS-specific cache path for non-checkout installations."""

        return user_cache_path("ansys-research-runner", appauthor="AnsysResearchRunner")
