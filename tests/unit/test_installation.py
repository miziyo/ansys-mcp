from __future__ import annotations

import re
from pathlib import Path

import ansys_research_runner.config as runner_config
from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.installation import (
    discover_ansys_installations,
    installation_version,
    resolve_ansys_installation,
)
from ansys_research_runner.portable_paths import portable_payload
from scripts.path_discovery import discover_ansys_root as discover_script_ansys_root


def test_runner_paths_follow_the_discovered_checkout_without_drive_assumptions(
    monkeypatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "relocated-checkout"
    monkeypatch.delenv("ANSYS_RESEARCH_ROOT", raising=False)
    monkeypatch.delenv("ANSYS_RESEARCH_RUNTIME", raising=False)
    monkeypatch.setattr(runner_config, "repository_root", lambda: checkout)

    paths = RunnerPaths.from_environment()

    assert paths.root == checkout
    assert paths.runtime == checkout / "runtime"
    assert paths.database == checkout / "runtime" / "jobs.sqlite"


def test_tracked_payload_paths_use_semantic_tokens() -> None:
    payload = {
        "repository": r"E:\project\runtime\result.json",
        "installation": r"C:\Program Files\ANSYS Inc\ANSYS Student\v261\bin\solver.exe",
        "work": r"F:\mapdl-qualification-work\attempt\file.rst",
        "profile": "D:" + r"\Users\someone\AppData\Local\Temp\trace.log",
        "workbench": (
            "C:" + r"\Users\someone\AppData\Local\Temp\WB_" + r"localuser_123_4\solve.out"
        ),
    }

    sanitized = portable_payload(payload)

    assert all(f"{drive}:" not in str(sanitized) for drive in "CDEF")
    assert "${ANSYS_INSTALLATION}" in sanitized["installation"]
    assert "${QUALIFICATION_WORK_ROOT}" in sanitized["work"]
    assert "${USER_HOME}" in sanitized["profile"]
    assert "WB_${USER_NAME}_${PROCESS_ID}_${INSTANCE_ID}" in sanitized["workbench"]


def test_explicit_installation_override_has_precedence(tmp_path: Path) -> None:
    explicit = tmp_path / "custom-installation"
    environment = {
        "ANSYS_RESEARCH_ANSYS_ROOT": str(explicit),
        "ProgramFiles": str(tmp_path / "program-files"),
    }

    assert resolve_ansys_installation(environment) == explicit.resolve()


def test_script_discovery_honors_explicit_override_before_newer_installation(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "custom"
    newer = tmp_path / "program-files" / "ANSYS Inc" / "v999"
    configured.mkdir()
    newer.mkdir(parents=True)

    resolved = discover_script_ansys_root(
        {
            "ANSYS_RESEARCH_ANSYS_ROOT": str(configured),
            "ProgramFiles": str(tmp_path / "program-files"),
        }
    )

    assert resolved == configured.resolve()


def test_discovers_newest_standard_or_student_installation(tmp_path: Path) -> None:
    program_files = tmp_path / "program-files"
    standard_252 = program_files / "ANSYS Inc" / "v252"
    student_261 = program_files / "ANSYS Inc" / "ANSYS Student" / "v261"
    standard_252.mkdir(parents=True)
    student_261.mkdir(parents=True)

    discovered = discover_ansys_installations({"ProgramFiles": str(program_files)})

    assert discovered == (student_261.resolve(), standard_252.resolve())
    assert resolve_ansys_installation({"ProgramFiles": str(program_files)}) == student_261.resolve()


def test_discovers_versioned_awp_root_without_fixed_version_name(tmp_path: Path) -> None:
    root = tmp_path / "v252"
    root.mkdir()

    assert resolve_ansys_installation({"AWP_ROOT252": str(root)}) == root.resolve()
    assert installation_version(root) == 252


def test_future_three_digit_release_is_selected_dynamically(tmp_path: Path) -> None:
    program_files = tmp_path / "program-files"
    current = program_files / "ANSYS Inc" / "ANSYS Student" / "v261"
    future = program_files / "ANSYS Inc" / "v262"
    environment_root = tmp_path / "v260"
    for root in (current, future, environment_root):
        root.mkdir(parents=True)

    discovered = discover_ansys_installations(
        {
            "ProgramFiles": str(program_files),
            "AWP_ROOT260": str(environment_root),
        }
    )

    assert discovered == (future.resolve(), current.resolve(), environment_root.resolve())
    assert resolve_ansys_installation({"ProgramFiles": str(program_files)}) == future.resolve()


def test_runtime_and_setup_surfaces_have_no_fixed_absolute_paths() -> None:
    root = Path(__file__).parents[2]
    candidates = [root / "README.md", root / ".env.example"]
    candidates.extend((root / "src").rglob("*.py"))
    candidates.extend((root / "scripts").rglob("*.py"))
    candidates.extend((root / "scripts").rglob("*.ps1"))
    candidates.extend((root / "docs").rglob("*.md"))
    windows_absolute = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\|/)")
    unix_home = re.compile(r"/(?:Users|home)/[^/<]", re.IGNORECASE)
    violations: list[str] = []
    for path in candidates:
        text = path.read_text("utf-8")
        if windows_absolute.search(text) or unix_home.search(text):
            violations.append(path.relative_to(root).as_posix())

    assert violations == []
