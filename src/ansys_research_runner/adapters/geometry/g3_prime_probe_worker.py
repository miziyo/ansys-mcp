"""Isolated live worker for probing the release-matched official PyPrimeMesh API."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any


def _stage(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _bounded_exit(client: Any, timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Attempt the public Prime client exit without hanging the probe."""

    outcome: dict[str, Any] = {}
    finished = threading.Event()

    def close() -> None:
        try:
            client.exit()
            outcome["completed"] = True
        except Exception as exc:
            outcome.update(
                {
                    "completed": False,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
        finally:
            finished.set()

    thread = threading.Thread(target=close, name="prime-client-exit", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if not finished.is_set():
        return {"completed": False, "reason": "CLIENT_EXIT_TIMEOUT"}
    return outcome


def _part_snapshot(part: Any, model: Any, prime: Any) -> dict[str, Any]:
    summary = part.get_summary(
        prime.PartSummaryParams(model=model, print_id=True, print_mesh=False)
    )
    return {
        "runtime_id": int(part.id),
        "display_name": str(part.name),
        "topo_face_ids": sorted(int(item) for item in part.get_topo_faces()),
        "topo_volume_ids": sorted(int(item) for item in part.get_topo_volumes()),
        "volume_ids": sorted(int(item) for item in part.get_volumes()),
        "summary": {
            "n_topo_edges": int(summary.n_topo_edges),
            "n_topo_faces": int(summary.n_topo_faces),
            "n_topo_volumes": int(summary.n_topo_volumes),
            "n_face_zonelets": int(summary.n_face_zonelets),
            "n_cell_zonelets": int(summary.n_cell_zonelets),
        },
    }


def execute(asset_path: Path, workdir: Path, route: str) -> dict[str, Any]:
    """Launch Prime 26.1, import one generated STEP, and report public topology data."""

    from ansys_research_runner.services.capability_service import resolve_ansys_root

    prime: Any = import_module("ansys.meshing.prime")
    if not asset_path.is_file():
        raise FileNotFoundError(asset_path)
    workdir.mkdir(parents=True, exist_ok=True)
    prime_root = (resolve_ansys_root() / "meshing" / "Prime").resolve()
    client = None
    try:
        _stage("launching:prime")
        client = prime.launch_prime(
            prime_root=str(prime_root),
            timeout=60.0,
            n_procs=1,
            version="26.1",
        )
        model = client.model
        _stage("importing:cad")
        params = prime.ImportCadParams(
            model=model,
            append=False,
            ansys_release="26.1",
            cad_reader_route=prime.CadReaderRoute[route.upper()],
            part_creation_type=prime.PartCreationType.BODY,
            geometry_transfer=True,
            length_unit=prime.LengthUnit.MM,
        )
        result = prime.FileIO(model=model).import_cad(str(asset_path.resolve()), params=params)
        error_code = str(result.error_code.name)
        _stage(f"imported:{error_code}")
        parts = [_part_snapshot(part, model, prime) for part in model.parts]
        details: dict[str, Any] = {
            "package_version": version("ansys-meshing-prime"),
            "prime_root": str(prime_root),
            "asset_path": str(asset_path.resolve()),
            "cad_reader_route": route,
            "import_error_code": error_code,
            "part_count": len(parts),
            "parts": parts,
            "part_public_geometry_methods": sorted(
                name
                for name in dir(prime.Part)
                if any(
                    token in name.lower()
                    for token in ("topo", "volume", "surface", "area", "centroid", "normal")
                )
            ),
        }
    except Exception:
        if client is not None:
            _bounded_exit(client)
        raise
    _stage("exiting")
    details["client_exit"] = _bounded_exit(client)
    _stage("complete")
    return {"ok": True, "details": details}


def main(argv: list[str] | None = None) -> int:
    """Run the isolated Prime probe and emit exactly one final JSON object."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument(
        "--route",
        choices=("native", "programcontrolled", "workbench", "spaceclaim", "discovery"),
        default="native",
    )
    args = parser.parse_args(argv)
    try:
        payload = execute(args.asset.resolve(), args.workdir.resolve(), args.route)
        return_code = 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
        return_code = 1
    print(json.dumps(payload, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
