"""Single-concurrency queue drainer launched by the MCP facade."""

from __future__ import annotations

import argparse
import os
import time

import portalocker

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.services.production_worker_service import (
    run_production_worker_once,
)


def main(argv: list[str] | None = None) -> int:
    """Drain queued jobs while holding the project-owned concurrency lock."""

    parser = argparse.ArgumentParser(
        description="Drain the local Ansys thermal Job Registry with concurrency one."
    )
    parser.parse_args(argv)
    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    lock_path = (paths.runtime / "production-worker.lock").resolve()
    try:
        with portalocker.Lock(lock_path, mode="a", timeout=3.0):
            idle_observations = 0
            while idle_observations < 5:
                result = run_production_worker_once(
                    paths=paths,
                    worker_id=f"production-dispatch-{os.getpid()}",
                )
                if result is None:
                    idle_observations += 1
                    time.sleep(0.2)
                else:
                    idle_observations = 0
    except portalocker.exceptions.LockException:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
