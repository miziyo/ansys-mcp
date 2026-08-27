"""Isolated official Ansys Prime CAD-to-MAPDL mesh worker."""

from __future__ import annotations

import argparse
import hashlib
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_exit(client: Any, timeout_seconds: float = 15.0) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    finished = threading.Event()

    def close() -> None:
        try:
            client.exit()
            outcome["completed"] = True
        except Exception as exc:
            outcome.update(
                completed=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            finished.set()

    thread = threading.Thread(target=close, name="prime-client-exit", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if not finished.is_set():
        return {"completed": False, "reason": "CLIENT_EXIT_TIMEOUT"}
    return outcome


def execute(asset_path: Path, output_path: Path, element_size_mm: float) -> dict[str, Any]:
    """Import one CAD source, create a tetrahedral volume mesh, and export MAPDL CDB."""

    from ansys_research_runner.services.capability_service import resolve_ansys_root

    if not asset_path.is_file():
        raise FileNotFoundError(asset_path)
    if element_size_mm <= 0.0:
        raise ValueError("element_size_mm must be positive")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prime: Any = import_module("ansys.meshing.prime")
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
        import_result = prime.FileIO(model=model).import_cad(
            str(asset_path.resolve()),
            params=prime.ImportCadParams(
                model=model,
                append=False,
                ansys_release="26.1",
                cad_reader_route=prime.CadReaderRoute.PROGRAMCONTROLLED,
                part_creation_type=prime.PartCreationType.BODY,
                geometry_transfer=True,
                length_unit=prime.LengthUnit.MM,
            ),
        )
        if import_result.error_code is not prime.ErrorCode.NOERROR:
            raise RuntimeError(f"Prime CAD import failed: {import_result.error_code.name}")
        mesh = prime.lucid.Mesh(model)
        _stage("meshing:surface")
        mesh.surface_mesh(min_size=element_size_mm, max_size=element_size_mm)
        _stage("meshing:volume")
        mesh.volume_mesh(volume_fill_type=prime.VolumeFillType.TET, quadratic=False)
        parts: list[dict[str, Any]] = []
        for part in model.parts:
            summary = part.get_summary(
                prime.PartSummaryParams(model=model, print_id=True, print_mesh=True)
            )
            parts.append(
                {
                    "runtime_id": int(part.id),
                    "name": str(part.name),
                    "nodes": int(summary.n_nodes),
                    "faces": int(summary.n_faces),
                    "cells": int(summary.n_cells),
                    "tet_cells": int(summary.n_tet_cells),
                    "unmeshed_topo_faces": int(summary.n_unmeshed_topo_faces),
                }
            )
        if not parts or sum(item["cells"] for item in parts) <= 0:
            raise RuntimeError("Prime produced no volume cells")
        _stage("exporting:cdb")
        export_result = prime.FileIO(model=model).export_mapdl_cdb(
            str(output_path.resolve()),
            prime.ExportMapdlCdbParams(model=model, write_cells=True, skip_comments=False),
        )
        export_error = str(export_result.error_code.name)
        if export_result.error_code is not prime.ErrorCode.NOERROR:
            raise RuntimeError(f"Prime CDB export failed: {export_error}")
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError("Prime reported success but no CDB file was produced")
        details: dict[str, Any] = {
            "package_version": version("ansys-meshing-prime"),
            "prime_root": str(prime_root),
            "source_path": str(asset_path.resolve()),
            "source_sha256": _sha256(asset_path),
            "element_size_mm": element_size_mm,
            "import_error_code": str(import_result.error_code.name),
            "export_error_code": export_error,
            "parts": parts,
            "cdb_path": str(output_path.resolve()),
            "cdb_size": output_path.stat().st_size,
            "cdb_sha256": _sha256(output_path),
        }
    except Exception:
        if client is not None:
            _bounded_exit(client)
        raise
    _stage("exiting:prime")
    details["client_exit"] = _bounded_exit(client)
    _stage("complete")
    return {"ok": True, "details": details}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--element-size-mm", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        payload = execute(args.asset.resolve(), args.output.resolve(), args.element_size_mm)
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
