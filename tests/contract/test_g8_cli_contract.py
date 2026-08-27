"""G8 CLI schema, command-surface, and architecture contracts."""

from __future__ import annotations

import ast
from pathlib import Path

from typer.main import get_command

from ansys_research_runner.cli import app
from ansys_research_runner.domain.application import CliResponse
from ansys_research_runner.services.schema_service import SCHEMA_MODELS

ROOT = Path(__file__).resolve().parents[2]


def test_g8_command_surface_is_complete() -> None:
    commands = set(get_command(app).commands)
    assert {
        "doctor",
        "inspect",
        "resolve",
        "validate",
        "plan",
        "run",
        "status",
        "cancel",
        "results",
        "artifacts",
        "recover",
    } <= commands


def test_cli_imports_only_the_application_service_boundary() -> None:
    source = (ROOT / "src" / "ansys_research_runner" / "cli.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    service_imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("ansys_research_runner.services")
    }
    assert service_imports == {"ansys_research_runner.services.application_service"}
    assert "ansys." not in source
    assert "subprocess" not in source


def test_cli_response_schema_is_versioned_and_committed() -> None:
    schema = CliResponse.model_json_schema(mode="validation")
    assert schema["properties"]["schema_version"]["const"] == 1
    assert set(schema["required"]) == {
        "command",
        "ok",
    }
    assert SCHEMA_MODELS["cli-response.v1.schema.json"] is CliResponse
