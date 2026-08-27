"""Export or verify the committed G2 JSON Schema artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from ansys_research_runner.services.schema_service import (
    SCHEMA_MODELS,
    check_schemas,
    export_schemas,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "schemas"


def main() -> int:
    """Export schemas by default, or verify them with ``--check``."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        problems = check_schemas(OUTPUT)
        if problems:
            print("\n".join(problems))
            return 1
        print(f"verified {len(SCHEMA_MODELS)} schema files")
        return 0
    paths = export_schemas(OUTPUT)
    print(f"exported {len(paths)} schemas to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
