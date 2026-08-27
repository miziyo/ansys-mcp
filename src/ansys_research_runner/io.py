"""Deterministic and atomic local I/O helpers."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def _replace_with_retry(temporary: Path, path: Path) -> None:
    """Replace a file despite brief Windows reader locks, without hiding persistent errors."""

    try:
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.025)
    finally:
        with suppress(OSError):
            temporary.unlink()


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    temporary.write_text(text, encoding="utf-8")
    _replace_with_retry(temporary, path)


def atomic_write_bytes(path: Path, value: bytes) -> None:
    """Atomically replace a binary file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    temporary.write_bytes(value)
    _replace_with_retry(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    """Atomically write deterministic JSON."""

    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")
