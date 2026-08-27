"""Normalize machine-local paths and trailing whitespace in tracked documentation."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from scripts.portable_paths import portable_string
except ModuleNotFoundError:  # Direct script execution adds only this directory to sys.path.
    from portable_paths import portable_string

ROOT = Path(__file__).resolve().parents[1]


def _sanitize_text(path: Path) -> bool:
    original = path.read_text("utf-8")
    sanitized = portable_string(original)
    had_final_newline = sanitized.endswith("\n")
    sanitized = "\n".join(line.rstrip() for line in sanitized.splitlines())
    if had_final_newline:
        sanitized += "\n"
    if original == sanitized:
        return False
    path.write_text(sanitized, "utf-8")
    return True


def main() -> int:
    changed = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "docs").rglob("*.md"))
        if _sanitize_text(path)
    ]
    print(json.dumps({"changed": changed, "count": len(changed)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
