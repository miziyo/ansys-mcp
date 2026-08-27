"""G0 package and CLI smoke tests."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from ansys_research_runner import __version__
from ansys_research_runner.cli import app
from ansys_research_runner.config import RunnerPaths


def test_package_version_is_public() -> None:
    assert __version__ == "0.13.0"


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "thermal research" in result.stdout


def test_cli_version_json() -> None:
    result = CliRunner().invoke(app, ["version", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "name": "ansys-research-runner",
        "version": "0.13.0",
    }


def test_runtime_paths_can_be_overridden(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANSYS_RESEARCH_ROOT", str(tmp_path))
    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    assert paths.root == tmp_path.resolve()
    assert paths.runs.is_dir()
    assert paths.gates.is_dir()


def test_geometry_doctor_static_json(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ANSYS_RESEARCH_ROOT", str(tmp_path))

    result = CliRunner().invoke(app, ["geometry-doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "geometry-doctor"
    assert payload["ok"] is True
    report = payload["data"]["report"]
    assert report["status"] == "BLOCKED_ENVIRONMENT"
    assert report["selected_backend"] is None
    assert (tmp_path / "runtime" / "geometry_capability_report.json").is_file()
