"""Portable serialization of machine-local paths in tracked records."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_ANSYS_INSTALLATION = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]Program Files[\\/]ANSYS Inc[\\/]"
    r"(?:ANSYS Student[\\/])?v\d{3}",
    re.IGNORECASE,
)
_QUALIFICATION_ROOT = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\\/\s\"']*"
    r"(?:qualification-work|connected-gallery-work)",
    re.IGNORECASE,
)
_WINDOWS_USER_HOME = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+",
    re.IGNORECASE,
)
_UNIX_USER_HOME = re.compile(r"/(?:Users|home)/[^/\s\"']+", re.IGNORECASE)
_LOCAL_DRIVE_PREFIX = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?=[\\/])")
_WORKBENCH_TEMP = re.compile(
    r"WB_[A-Za-z0-9_.-]+_(?P<process>\d+)_(?P<instance>\d+)",
    re.IGNORECASE,
)


def portable_string(value: str) -> str:
    """Replace local roots in a string with stable semantic tokens."""

    result = value
    repository_variants = {
        str(REPOSITORY_ROOT),
        str(REPOSITORY_ROOT).replace("\\", "/"),
        str(REPOSITORY_ROOT).replace("/", "\\"),
    }
    for repository in sorted(repository_variants, key=len, reverse=True):
        result = result.replace(repository, "${REPOSITORY_ROOT}")
    hostname = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
    if hostname:
        result = re.sub(re.escape(hostname), "${HOSTNAME}", result, flags=re.IGNORECASE)
    result = _ANSYS_INSTALLATION.sub("${ANSYS_INSTALLATION}", result)
    result = _QUALIFICATION_ROOT.sub("${QUALIFICATION_WORK_ROOT}", result)
    result = _WINDOWS_USER_HOME.sub("${USER_HOME}", result)
    result = _UNIX_USER_HOME.sub("${USER_HOME}", result)
    result = _WORKBENCH_TEMP.sub(
        "WB_${USER_NAME}_${PROCESS_ID}_${INSTANCE_ID}",
        result,
    )
    return _LOCAL_DRIVE_PREFIX.sub("${LOCAL_DRIVE_ROOT}", result)


def portable_payload(value: Any) -> Any:
    """Recursively sanitize strings in one JSON-compatible payload."""

    if isinstance(value, str):
        return portable_string(value)
    if isinstance(value, list):
        return [portable_payload(item) for item in value]
    if isinstance(value, tuple):
        return [portable_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): portable_payload(item) for key, item in value.items()}
    return value
