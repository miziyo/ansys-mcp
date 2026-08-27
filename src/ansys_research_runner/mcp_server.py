"""Thin local-STDIO MCP facade over the research-runner application boundary."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable
from typing import Any, Protocol

from ansys.common.mcp import PyAnsysBaseAppContext, PyAnsysBaseMCP
from pydantic import BaseModel

from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.services.application_service import ResearchRunnerApplication
from ansys_research_runner.services.production_worker_service import BackgroundWorkerDispatcher


class RunDispatcher(Protocol):
    """Minimal non-blocking queue-dispatch boundary used by ``start_run``."""

    def dispatch(self) -> None:
        """Ensure a detached Job Registry worker is draining the queue."""
        ...


def _payload(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=False)


def _success(data: BaseModel) -> dict[str, Any]:
    return {"schema_version": 1, "ok": True, "data": _payload(data), "error": None}


def _success_data(data: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "ok": True, "data": data, "error": None}


def _failure(error: DomainError) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": False,
        "data": None,
        "error": {
            "code": error.code.value,
            "path": error.path,
            "message": error.message,
            "details": error.details,
        },
    }


class AnsysResearchRunnerMCP(PyAnsysBaseMCP):
    """Unified official-base MCP that never owns an interactive solver session."""

    def __init__(
        self,
        *,
        application: ResearchRunnerApplication | None = None,
        dispatcher: RunDispatcher | None = None,
    ) -> None:
        self.application = application or ResearchRunnerApplication()
        self.dispatcher = dispatcher or BackgroundWorkerDispatcher(self.application.paths)
        super().__init__(
            name="ansys-research-runner",
            instructions=(
                "Submit supported thermal jobs through the durable Job Registry. "
                "Field arrays remain in artifacts and arbitrary code execution is unavailable."
            ),
            need_python=False,
        )
        self._register_tools()

    def create_context(self) -> PyAnsysBaseAppContext:
        """Expose metadata only; no persistent Python or Ansys session is created."""

        return PyAnsysBaseAppContext(
            metadata={
                "transport": "stdio",
                "solver_session_owned": False,
                "job_registry": str(self.application.paths.database),
            }
        )

    def product_startup(self) -> None:
        """Initialize no product session; workers are external registry consumers."""

    def product_cleanup(self) -> None:
        """Leave already-submitted detached jobs under registry supervision."""

    @staticmethod
    def _call(operation: Callable[[], BaseModel]) -> dict[str, Any]:
        try:
            return _success(operation())
        except DomainError as exc:
            return _failure(exc)
        except Exception as exc:  # noqa: BLE001 - MCP boundary hides implementation tracebacks
            return _failure(
                DomainError(
                    ErrorCode.INTERNAL_ERROR,
                    "mcp",
                    "Unexpected application failure.",
                    details={"error_type": type(exc).__name__},
                )
            )

    def _register_tools(self) -> None:
        @self.tool(name="doctor")
        def doctor(live: bool = False, timeout_seconds: float = 30.0) -> dict[str, Any]:
            """Report installed host and PyAnsys capabilities."""

            return self._call(
                lambda: self.application.doctor(
                    live=live,
                    timeout_seconds=timeout_seconds,
                )
            )

        @self.tool(name="inspect_model")
        def inspect_model(model_path: str) -> dict[str, Any]:
            """Inspect one confined supported CAD model into a Geometry Graph."""

            return self._call(lambda: self.application.inspect(model_path))

        @self.tool(name="resolve_regions")
        def resolve_regions(recipe_path: str) -> dict[str, Any]:
            """Resolve semantic regions referenced by one Run Recipe."""

            return self._call(lambda: self.application.resolve(recipe_path))

        @self.tool(name="validate_run")
        def validate_run(recipe_path: str) -> dict[str, Any]:
            """Validate one Run Recipe and all referenced contracts."""

            return self._call(lambda: self.application.validate(recipe_path))

        @self.tool(name="plan_run")
        def plan_run(recipe_path: str, run_id: str | None = None) -> dict[str, Any]:
            """Compile a supported recipe into immutable reviewed CAE-IR."""

            return self._call(lambda: self.application.plan(recipe_path, run_id=run_id))

        @self.tool(name="start_run")
        def start_run(recipe_path: str, run_id: str | None = None) -> dict[str, Any]:
            """Enqueue a run and immediately return its durable QUEUED snapshot."""

            try:
                result = self.application.run(recipe_path, run_id=run_id)
            except DomainError as exc:
                return _failure(exc)
            except Exception as exc:  # noqa: BLE001 - MCP boundary hides internal traceback
                return _failure(
                    DomainError(
                        ErrorCode.INTERNAL_ERROR,
                        "start_run",
                        "Unexpected application failure.",
                        details={"error_type": type(exc).__name__},
                    )
                )
            response = _success_data(
                {
                    "schema_version": 1,
                    "run_id": result.job.job_id,
                    "status": result.job.status.value,
                }
            )
            try:
                self.dispatcher.dispatch()
            except OSError as exc:
                return _failure(
                    DomainError(
                        ErrorCode.INTERNAL_ERROR,
                        "start_run.dispatch",
                        "The job was queued, but the background worker could not start.",
                        details={
                            "error_type": type(exc).__name__,
                            "run_id": result.job.job_id,
                            "status": result.job.status.value,
                        },
                    )
                )
            return response

        @self.tool(name="get_run_status")
        def get_run_status(run_id: str) -> dict[str, Any]:
            """Return a job snapshot and its append-only event history."""

            try:
                result = self.application.status(run_id)
            except DomainError as exc:
                return _failure(exc)
            job = result.job
            return _success_data(
                {
                    "schema_version": 1,
                    "job": {
                        "run_id": job.job_id,
                        "kind": job.kind,
                        "status": job.status.value,
                        "created_at": job.created_at,
                        "updated_at": job.updated_at,
                        "attempt": job.attempt,
                        "error_code": job.error_code,
                        "error_message": job.error_message,
                        "worker_result": job.result,
                    },
                    "events": [event.model_dump(mode="json") for event in result.events],
                }
            )

        @self.tool(name="cancel_run")
        def cancel_run(run_id: str) -> dict[str, Any]:
            """Request safe cancellation through the Job Registry."""

            try:
                result = self.application.cancel(run_id)
            except DomainError as exc:
                return _failure(exc)
            return _success_data(
                {
                    "schema_version": 1,
                    "run_id": result.job.job_id,
                    "status": result.job.status.value,
                }
            )

        @self.tool(name="get_run_summary")
        def get_run_summary(run_id: str) -> dict[str, Any]:
            """Return bounded scalar results without any field arrays."""

            return self._call(lambda: self.application.results(run_id))

        @self.tool(name="list_run_artifacts")
        def list_run_artifacts(run_id: str) -> dict[str, Any]:
            """List artifact paths, hashes, media types, and sizes only."""

            return self._call(lambda: self.application.artifacts(run_id))

    def run_cli(self, argv: list[str] | None = None) -> None:
        """Run the v0.x server over local STDIO; HTTP is intentionally unavailable."""

        parser = argparse.ArgumentParser(description="Run the unified Ansys research MCP server.")
        parser.add_argument(
            "--transport",
            choices=("stdio",),
            default="stdio",
            help="v0.x supports local STDIO only.",
        )
        parser.parse_args(argv)
        asyncio.run(self.run_async(transport="stdio", show_banner=False))


# Backward-compatible import retained for clients built against the thermal-only name.
ThermalResearchRunnerMCP = AnsysResearchRunnerMCP


def main(argv: list[str] | None = None) -> int:
    """Start the local STDIO MCP server."""

    AnsysResearchRunnerMCP().run_cli(argv)
    return 0


def entrypoint() -> None:
    """Console-script entry point."""

    raise SystemExit(main())


if __name__ == "__main__":
    sys.exit(main())
