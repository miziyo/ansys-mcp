"""Logging defaults shared by CLI, worker, and MCP entry points."""

from __future__ import annotations

import logging
import time


def configure_logging(*, verbose: bool = False) -> None:
    """Configure concise UTC process logging."""

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    logging.Formatter.converter = time.gmtime
