"""Location-independent defaults for repository maintenance scripts."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

_VERSION = re.compile(r"^v?(\d{3})$", re.IGNORECASE)
_AWP_ROOT = re.compile(r"^AWP_ROOT\d{3}$", re.IGNORECASE)


def discover_ansys_root(
    environment: Mapping[str, str] | None = None,
    *,
    required: str | Path | None = None,
) -> Path:
    """Return the newest discovered Ansys root containing an optional relative path."""

    env = environment if environment is not None else os.environ
    explicit = env.get("ANSYS_RESEARCH_ANSYS_ROOT")
    relative = Path(required) if required is not None else None
    if explicit:
        configured = Path(explicit).expanduser()
        if configured.is_dir() and (relative is None or (configured / relative).exists()):
            return configured.resolve()
        requirement = f" containing {relative}" if relative is not None else ""
        raise FileNotFoundError(
            f"Configured Ansys installation does not exist{requirement}: {configured}"
        )

    candidates: list[Path] = []
    candidates.extend(
        Path(value).expanduser()
        for name, value in env.items()
        if _AWP_ROOT.fullmatch(name) and value
    )
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        value = env.get(name)
        if not value:
            continue
        vendor = Path(value).expanduser() / "ANSYS Inc"
        candidates.extend(vendor.glob("ANSYS Student/v*"))
        candidates.extend(vendor.glob("v*"))

    installed = {
        str(candidate.resolve()).casefold(): candidate.resolve()
        for candidate in candidates
        if candidate.is_dir() and (relative is None or (candidate / relative).exists())
    }
    if not installed:
        requirement = f" containing {relative}" if relative is not None else ""
        raise FileNotFoundError(
            "Could not automatically discover an Ansys installation"
            f"{requirement}. Set ANSYS_RESEARCH_ANSYS_ROOT to override discovery."
        )

    def sort_key(path: Path) -> tuple[int, bool, str]:
        match = _VERSION.fullmatch(path.name)
        return (
            int(match.group(1)) if match else -1,
            "ansys student" in str(path).casefold(),
            str(path).casefold(),
        )

    return max(installed.values(), key=sort_key)


def default_work_root(name: str, environment: Mapping[str, str] | None = None) -> Path:
    """Return an OS temporary work root, optionally overridden by one common environment value."""

    env = environment if environment is not None else os.environ
    configured = env.get("ANSYS_RESEARCH_WORK_ROOT")
    base = Path(configured).expanduser() if configured else Path(tempfile.gettempdir())
    return (base / "ansys-research-runner" / name).resolve()
