"""Atomic local-file replacement regression tests."""

from __future__ import annotations

import os
from pathlib import Path

from ansys_research_runner import io


def test_atomic_write_retries_brief_windows_reader_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "heartbeat.txt"
    target.write_text("old\n", encoding="utf-8")
    real_replace = os.replace
    attempts = 0

    def briefly_locked(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated reader lock")
        real_replace(source, destination)

    monkeypatch.setattr(io.os, "replace", briefly_locked)

    io.atomic_write_text(target, "new\n")

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == "new\n"
    assert not tuple(tmp_path.glob("*.tmp"))
