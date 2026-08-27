"""Compatibility import for portable tracked-report serialization."""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from ansys_research_runner.portable_paths import portable_payload, portable_string
except ModuleNotFoundError:
    source_root = Path(__file__).resolve().parents[1] / "src"
    sys.path.insert(0, str(source_root))
    from ansys_research_runner.portable_paths import portable_payload, portable_string

__all__ = ["portable_payload", "portable_string"]
