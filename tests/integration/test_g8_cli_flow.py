"""G8 end-to-end CLI flow and required security rejection matrix."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml
from typer.testing import CliRunner

import ansys_research_runner.cli as cli_module
from ansys_research_runner.cli import app
from tests.g8_fixtures import build_g8_workspace, write_yaml


def _install_application(monkeypatch, service) -> CliRunner:
    monkeypatch.setattr(cli_module, "application_factory", lambda: service)
    return CliRunner()


def _invoke_json(runner: CliRunner, arguments: list[str]) -> tuple[object, dict[str, object]]:
    result = runner.invoke(app, [*arguments, "--json"])
    return result, json.loads(result.stdout)


def _assert_error(result, payload: dict[str, object], code: str) -> None:
    assert result.exit_code != 0
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == code


def test_all_g8_commands_complete_a_durable_nonblocking_job_flow(
    monkeypatch, tmp_path: Path
) -> None:
    service, recipe_path, model_path = build_g8_workspace(tmp_path)
    monkeypatch.setenv("ANSYS_RESEARCH_ROOT", str(service.paths.root))
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["doctor"])
    assert result.exit_code == 0
    assert payload["command"] == "doctor"

    result, payload = _invoke_json(runner, ["inspect", str(model_path)])
    assert result.exit_code == 0
    assert payload["data"]["backend"] == "synthetic"  # type: ignore[index]

    for command in ("resolve", "validate", "plan"):
        result, payload = _invoke_json(runner, [command, str(recipe_path)])
        assert result.exit_code == 0
        assert payload["command"] == command
        assert payload["ok"] is True

    result, payload = _invoke_json(
        runner,
        ["run", str(recipe_path), "--run-id", "g8-e2e"],
    )
    assert result.exit_code == 0
    assert payload["data"]["job"]["status"] == "QUEUED"  # type: ignore[index]

    result, payload = _invoke_json(runner, ["status", "g8-e2e"])
    assert result.exit_code == 0
    assert payload["data"]["events"][0]["event_type"] == "JOB_CREATED"  # type: ignore[index]

    result, payload = _invoke_json(runner, ["results", "g8-e2e"])
    assert result.exit_code == 0
    assert payload["data"]["summary"] is None  # type: ignore[index]

    result, payload = _invoke_json(runner, ["artifacts", "g8-e2e"])
    assert result.exit_code == 0
    assert payload["data"]["artifacts"] == []  # type: ignore[index]

    result, payload = _invoke_json(runner, ["cancel", "g8-e2e"])
    assert result.exit_code == 0
    assert payload["data"]["job"]["status"] == "CANCELLED"  # type: ignore[index]

    result, payload = _invoke_json(runner, ["recover"])
    assert result.exit_code == 0
    assert payload["data"]["recovered"] == []  # type: ignore[index]


def test_security_rejects_root_escape(monkeypatch, tmp_path: Path) -> None:
    service, _, _ = build_g8_workspace(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("schema_version: 1\n", encoding="utf-8")
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(outside)])

    _assert_error(result, payload, "PATH_OUTSIDE_ROOT")


def test_security_rejects_parent_traversal(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    traversal = recipe_path.parent / "nested" / ".." / recipe_path.name
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(traversal)])

    _assert_error(result, payload, "PATH_TRAVERSAL")


def test_security_rejects_link_escape(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy2(recipe_path, outside / recipe_path.name)
    link = recipe_path.parent / "outside-link"
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(link / recipe_path.name)])

    _assert_error(result, payload, "SYMLINK_ESCAPE")


def test_security_rejects_unsupported_extension(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    unsupported = recipe_path.with_suffix(".txt")
    shutil.copy2(recipe_path, unsupported)
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(unsupported)])

    _assert_error(result, payload, "UNSUPPORTED_EXTENSION")


def test_security_rejects_oversized_yaml(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    recipe_path.write_text("x" * 1_048_577, encoding="utf-8")
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(recipe_path)])

    _assert_error(result, payload, "YAML_TOO_LARGE")


def test_security_rejects_unsafe_yaml_tag(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    recipe_path.write_text(
        "!!python/object/apply:os.system ['echo unsafe']",
        encoding="utf-8",
    )
    runner = _install_application(monkeypatch, service)

    result, payload = _invoke_json(runner, ["validate", str(recipe_path)])

    _assert_error(result, payload, "YAML_UNSAFE")


def test_security_rejects_invalid_units(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    payload = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    payload["materials"]["thermal_domain"]["thermal_conductivity"] = 15
    write_yaml(recipe_path, payload)
    runner = _install_application(monkeypatch, service)

    result, response = _invoke_json(runner, ["validate", str(recipe_path)])

    _assert_error(result, response, "UNIT_REQUIRED")


def test_security_rejects_unknown_selector(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    manifest_path = recipe_path.parent / "box.manifest.yaml"
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    payload["roles"]["thermal_domain"]["selector"] = {"unknown_selector": True}
    write_yaml(manifest_path, payload)
    runner = _install_application(monkeypatch, service)

    result, response = _invoke_json(runner, ["validate", str(recipe_path)])

    _assert_error(result, response, "INVALID_SELECTOR")


def test_security_rejects_arbitrary_script_field(monkeypatch, tmp_path: Path) -> None:
    service, recipe_path, _ = build_g8_workspace(tmp_path)
    payload = yaml.safe_load(recipe_path.read_text(encoding="utf-8"))
    payload["script"] = "import os"
    write_yaml(recipe_path, payload)
    runner = _install_application(monkeypatch, service)

    result, response = _invoke_json(runner, ["validate", str(recipe_path)])

    _assert_error(result, response, "ARBITRARY_SCRIPT_FIELD")
