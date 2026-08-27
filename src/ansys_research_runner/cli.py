"""Thin command-line facade over the research-runner application service."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from ansys_research_runner import __version__
from ansys_research_runner.domain.application import (
    CliCommand,
    CliResponse,
    CommandData,
    CommandError,
)
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.logging_config import configure_logging
from ansys_research_runner.services.application_service import ResearchRunnerApplication

app = typer.Typer(
    name="ansys-research",
    help="Run and verify supported Ansys thermal research cases.",
    no_args_is_help=True,
)

application_factory: Callable[[], ResearchRunnerApplication] = ResearchRunnerApplication


@app.callback()
def configure(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging."),
    ] = False,
) -> None:
    """Configure process-wide CLI behavior."""

    configure_logging(verbose=verbose)


def _exit_code(code: ErrorCode) -> int:
    if code is ErrorCode.CAPABILITY_UNAVAILABLE:
        return 3
    if code in {
        ErrorCode.JOB_NOT_FOUND,
        ErrorCode.JOB_CONFLICT,
        ErrorCode.JOB_STATE_INVALID,
    }:
        return 4
    if code is ErrorCode.INTERNAL_ERROR:
        return 10
    return 2


def _render(response: CliResponse, *, json_output: bool) -> None:
    payload = response.model_dump(mode="json", by_alias=True, exclude_none=False)
    if json_output:
        typer.echo(json.dumps(payload, separators=(",", ":"), sort_keys=True))
        return
    if response.ok:
        typer.echo(json.dumps(payload["data"], indent=2, ensure_ascii=False, sort_keys=True))
        return
    assert response.error is not None
    typer.echo(
        f"{response.error.code} [{response.error.path}]: {response.error.message}",
        err=True,
    )


def _dispatch(
    command: CliCommand,
    *,
    json_output: bool,
    operation: Callable[[ResearchRunnerApplication], CommandData],
) -> None:
    try:
        data = operation(application_factory())
    except DomainError as exc:
        response = CliResponse.failure(
            command,
            CommandError(
                code=exc.code.value,
                path=exc.path,
                message=exc.message,
                details=exc.details,
            ),
        )
        _render(response, json_output=json_output)
        raise typer.Exit(code=_exit_code(exc.code)) from exc
    except Exception as exc:  # noqa: BLE001 - CLI boundary hides implementation traceback
        response = CliResponse.failure(
            command,
            CommandError(
                code=ErrorCode.INTERNAL_ERROR.value,
                path=command.value,
                message="Unexpected application failure.",
                details={"error_type": type(exc).__name__},
            ),
        )
        _render(response, json_output=json_output)
        raise typer.Exit(code=10) from exc
    _render(CliResponse.success(command, data), json_output=json_output)


@app.command()
def version(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a stable JSON object."),
    ] = False,
) -> None:
    """Print the runner version."""

    payload = {"name": "ansys-research-runner", "version": __version__}
    typer.echo(json.dumps(payload, sort_keys=True) if json_output else __version__)


@app.command()
def doctor(
    live: Annotated[
        bool,
        typer.Option("--live", help="Launch bounded Workbench and Mechanical probes."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned capability response."),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout", min=1, help="Timeout for each live product probe."),
    ] = 180,
) -> None:
    """Inspect the host, official packages, and optional live products."""

    _dispatch(
        CliCommand.DOCTOR,
        json_output=json_output,
        operation=lambda service: service.doctor(
            live=live,
            timeout_seconds=timeout_seconds,
        ),
    )


@app.command("geometry-doctor")
def geometry_doctor(
    live: Annotated[
        bool,
        typer.Option("--live", help="Launch bounded Geometry and Prime probes."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned capability response."),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout", min=1, help="Timeout for each live backend probe."),
    ] = 90,
) -> None:
    """Assess official Geometry backends against the G3 contract."""

    _dispatch(
        CliCommand.GEOMETRY_DOCTOR,
        json_output=json_output,
        operation=lambda service: service.geometry_doctor(
            live=live,
            timeout_seconds=timeout_seconds,
        ),
    )


@app.command("solver-doctor")
def solver_doctor(
    live: Annotated[
        bool,
        typer.Option("--live", help="Launch bounded Prime/MAPDL/DPF probes."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned capability response."),
    ] = False,
    timeout_seconds: Annotated[
        float,
        typer.Option("--timeout", min=1, help="Timeout for each launch probe."),
    ] = 180,
) -> None:
    """Assess official Prime/MAPDL/DPF thermal-solve readiness."""

    _dispatch(
        CliCommand.SOLVER_DOCTOR,
        json_output=json_output,
        operation=lambda service: service.solver_doctor(
            live=live,
            timeout_seconds=timeout_seconds,
        ),
    )


@app.command()
def inspect(
    model: Annotated[Path, typer.Argument(help="Model file under the configured input root.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned inspection response."),
    ] = False,
) -> None:
    """Inspect a model into the solver-neutral Geometry Graph."""

    _dispatch(
        CliCommand.INSPECT,
        json_output=json_output,
        operation=lambda service: service.inspect(model),
    )


@app.command()
def resolve(
    recipe: Annotated[Path, typer.Argument(help="Run Recipe YAML under the input root.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned resolution response."),
    ] = False,
) -> None:
    """Resolve semantic regions for one Run Recipe."""

    _dispatch(
        CliCommand.RESOLVE,
        json_output=json_output,
        operation=lambda service: service.resolve(recipe),
    )


@app.command()
def validate(
    recipe: Annotated[Path, typer.Argument(help="Run Recipe YAML under the input root.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned validation response."),
    ] = False,
) -> None:
    """Run cross-contract preflight validation."""

    _dispatch(
        CliCommand.VALIDATE,
        json_output=json_output,
        operation=lambda service: service.validate(recipe),
    )


@app.command()
def plan(
    recipe: Annotated[Path, typer.Argument(help="Run Recipe YAML under the input root.")],
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional deterministic plan identifier."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned CAE-IR plan response."),
    ] = False,
) -> None:
    """Compile a validated recipe into solver-bound CAE-IR."""

    _dispatch(
        CliCommand.PLAN,
        json_output=json_output,
        operation=lambda service: service.plan(recipe, run_id=run_id),
    )


@app.command()
def run(
    recipe: Annotated[Path, typer.Argument(help="Run Recipe YAML under the input root.")],
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional durable job identifier."),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned job-submission response."),
    ] = False,
) -> None:
    """Validate, compile, and enqueue a thermal job without blocking."""

    _dispatch(
        CliCommand.RUN,
        json_output=json_output,
        operation=lambda service: service.run(recipe, run_id=run_id),
    )


@app.command()
def status(
    run_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned job-status response."),
    ] = False,
) -> None:
    """Read a job snapshot and its immutable event history."""

    _dispatch(
        CliCommand.STATUS,
        json_output=json_output,
        operation=lambda service: service.status(run_id),
    )


@app.command()
def cancel(
    run_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned cancellation response."),
    ] = False,
) -> None:
    """Request safe cancellation through the durable job registry."""

    _dispatch(
        CliCommand.CANCEL,
        json_output=json_output,
        operation=lambda service: service.cancel(run_id),
    )


@app.command()
def results(
    run_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned small-result response."),
    ] = False,
) -> None:
    """Return small results while leaving field arrays in artifacts."""

    _dispatch(
        CliCommand.RESULTS,
        json_output=json_output,
        operation=lambda service: service.results(run_id),
    )


@app.command()
def artifacts(
    run_id: Annotated[str, typer.Argument(help="Durable job identifier.")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit versioned artifact metadata."),
    ] = False,
) -> None:
    """List integrity metadata for job-owned artifacts."""

    _dispatch(
        CliCommand.ARTIFACTS,
        json_output=json_output,
        operation=lambda service: service.artifacts(run_id),
    )


@app.command()
def recover(
    heartbeat_grace_s: Annotated[
        float,
        typer.Option("--heartbeat-grace", min=0, help="Additional stale-heartbeat grace."),
    ] = 0,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit a versioned recovery response."),
    ] = False,
) -> None:
    """Recover expired worker leases without crossing the solve boundary."""

    _dispatch(
        CliCommand.RECOVER,
        json_output=json_output,
        operation=lambda service: service.recover(heartbeat_grace_s=heartbeat_grace_s),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the CLI without forcing callers through ``SystemExit``."""

    try:
        result = app(args=argv, prog_name="ansys-research", standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    return int(result) if isinstance(result, int) else 0


def entrypoint() -> None:
    """Console-script entry point."""

    raise SystemExit(main())
