"""Automatic discovery of local Ansys installations."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_VERSION_DIRECTORY = re.compile(r"^v?(?P<version>\d{3})$", re.IGNORECASE)
_AWP_ROOT = re.compile(r"^AWP_ROOT(?P<version>\d{3})$", re.IGNORECASE)


def installation_version(root: Path) -> int | None:
    """Return an Ansys three-digit version code inferred from an installation directory."""

    match = _VERSION_DIRECTORY.fullmatch(root.name)
    return int(match.group("version")) if match else None


def _program_files_directories(environment: Mapping[str, str]) -> list[Path]:
    directories: list[Path] = []
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = environment.get(name)
        if value:
            candidate = Path(value).expanduser()
            if candidate not in directories:
                directories.append(candidate)
    return directories


def _installed_candidates(environment: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    for name, value in environment.items():
        if _AWP_ROOT.fullmatch(name) and value:
            candidates.append(Path(value).expanduser())

    for program_files in _program_files_directories(environment):
        vendor = program_files / "ANSYS Inc"
        candidates.extend(vendor.glob("ANSYS Student/v*"))
        candidates.extend(vendor.glob("v*"))

    unique: dict[str, Path] = {}
    for candidate in candidates:
        if candidate.is_dir():
            resolved = candidate.resolve()
            unique.setdefault(str(resolved).casefold(), resolved)
    return list(unique.values())


def discover_ansys_installations(
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Discover installed Ansys roots, newest version first, without launching a product."""

    env = environment if environment is not None else os.environ
    candidates = _installed_candidates(env)
    candidates.sort(
        key=lambda path: (
            installation_version(path) or -1,
            "ansys student" in str(path).casefold(),
            str(path).casefold(),
        ),
        reverse=True,
    )
    return tuple(candidates)


def resolve_ansys_installation(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve an explicit override or the newest automatically discovered installation root."""

    env = environment if environment is not None else os.environ
    configured = env.get("ANSYS_RESEARCH_ANSYS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    discovered = discover_ansys_installations(env)
    if discovered:
        return discovered[0]

    program_files = _program_files_directories(env)
    if program_files:
        return (program_files[0] / "ANSYS Inc").resolve()
    return (Path.home() / "Ansys-not-detected").resolve()
