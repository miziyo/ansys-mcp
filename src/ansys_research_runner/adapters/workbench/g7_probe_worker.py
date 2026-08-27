"""Isolated actual PyWorkbench lifecycle and Mechanical handoff worker."""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from ansys_research_runner.adapters.workbench.pyworkbench import (
    execute_workbench_coupling_probe,
)


def main(argv: list[str] | None = None) -> int:
    """Execute the reviewed G7 probe and emit one structured payload."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--mode", choices=("lifecycle", "coupling"), required=True)
    args = parser.parse_args(argv)
    try:
        payload = {
            "ok": True,
            "details": execute_workbench_coupling_probe(
                args.workdir.resolve(), attempt_handoff=args.mode == "coupling"
            ),
        }
        return_code = 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        return_code = 2
    print(json.dumps(payload, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
