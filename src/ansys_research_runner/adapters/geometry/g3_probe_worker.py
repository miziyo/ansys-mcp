"""Isolated live worker for probing the installed official Geometry API."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import Any


def _stage(message: str) -> None:
    """Emit non-protocol progress to stderr for timeout diagnosis."""

    print(message, file=sys.stderr, flush=True)


def _floats(value: Any) -> list[float]:
    return [float(item) for item in value]


def _attempt(getter: Any) -> dict[str, Any]:
    try:
        value = getter()
        return {"available": True, "value": _floats(value)}
    except Exception as exc:
        return {
            "available": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _face_inventory(face: Any) -> dict[str, Any]:
    from ansys.geometry.core.misc.units import UNITS

    bounds = face.bounding_box
    return {
        "runtime_id": str(face.id),
        "surface_type": str(face.surface_type.name),
        "area_m2": float(face.area.to(UNITS.m**2).magnitude),
        "bounding_box_min_m": _floats(bounds.min_corner),
        "bounding_box_max_m": _floats(bounds.max_corner),
        "bounding_box_center_m": _floats(bounds.center),
        "centroid": _attempt(lambda: face.centroid),
    }


def _body_inventory(body: Any) -> dict[str, Any]:
    from ansys.geometry.core.misc.units import UNITS

    bounds = body.bounding_box
    faces = [_face_inventory(face) for face in body.faces]
    return {
        "runtime_id": str(body.id),
        "display_name": str(body.name),
        "solid": not bool(body.is_surface),
        "volume_m3": float(body.volume.to(UNITS.m**3).magnitude),
        "surface_area_m2": sum(float(item["area_m2"]) for item in faces),
        "bounding_box_min_m": _floats(bounds.min_corner),
        "bounding_box_max_m": _floats(bounds.max_corner),
        "bounding_box_center_m": _floats(bounds.center),
        "centroid": _attempt(lambda: body.centroid),
        "face_count": len(faces),
        "faces": faces,
    }


def _shape_attempt(face: Any) -> dict[str, Any]:
    """Try the public orientation path without substituting inferred geometry."""

    try:
        geometry = face.shape.geometry
        axis = getattr(geometry, "dir_z", None)
        surface_origin = getattr(geometry, "origin", None)
        return {
            "available": True,
            "surface_class": type(geometry).__name__,
            "normal": _floats(face.normal()),
            "representative_point": _floats(face.point()),
            "surface_axis": None if axis is None else _floats(axis),
            "surface_origin": None if surface_origin is None else _floats(surface_origin),
        }
    except Exception as exc:
        return {
            "available": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        }


def _launch(backend: str, workdir: Path) -> Any:
    from ansys.geometry.core.connection.backend import ApiVersions
    from ansys.geometry.core.connection.launcher import (
        launch_modeler_with_discovery,
        launch_modeler_with_spaceclaim,
    )

    launcher = (
        launch_modeler_with_discovery if backend == "discovery" else launch_modeler_with_spaceclaim
    )
    return launcher(
        version=261,
        api_version=ApiVersions.V_261,
        hidden=True,
        server_working_dir=workdir,
    )


def _bounded_exit(modeler: Any, timeout_seconds: float = 10.0) -> dict[str, Any]:
    """Attempt the public client exit without allowing it to hang the probe."""

    outcome: dict[str, Any] = {}
    finished = threading.Event()

    def close() -> None:
        try:
            modeler.exit()
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

    thread = threading.Thread(target=close, name="geometry-client-exit", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if not finished.is_set():
        return {"completed": False, "reason": "CLIENT_EXIT_TIMEOUT"}
    return outcome


def _create_box(modeler: Any) -> tuple[Any, Any]:
    from ansys.geometry.core.math.point import Point2D
    from ansys.geometry.core.misc.units import UNITS
    from ansys.geometry.core.sketch.sketch import Sketch

    design = modeler.create_design("ThermalRunner-G3-Box")
    sketch = Sketch().box(
        Point2D([0.0, 0.0], unit=UNITS.m),
        width=2.0 * UNITS.m,
        height=3.0 * UNITS.m,
    )
    body = design.extrude_sketch("G3_Box", sketch, 4.0 * UNITS.m)
    if body is None:
        raise RuntimeError("Official Geometry API returned no body for box extrusion.")
    return design, body


def _create_cylinder(modeler: Any) -> tuple[Any, Any]:
    from ansys.geometry.core.math.point import Point2D
    from ansys.geometry.core.misc.units import UNITS
    from ansys.geometry.core.sketch.sketch import Sketch

    design = modeler.create_design("ThermalRunner-G3-Cylinder")
    sketch = Sketch().circle(
        Point2D([0.0, 0.0], unit=UNITS.m),
        radius=0.5 * UNITS.m,
    )
    body = design.extrude_sketch("G3_Cylinder", sketch, 2.0 * UNITS.m)
    if body is None:
        raise RuntimeError("Official Geometry API returned no body for cylinder extrusion.")
    return design, body


def execute(
    backend: str,
    workdir: Path,
    mode: str,
    asset: str,
    source: Path | None = None,
) -> dict[str, Any]:
    """Launch one backend, create both test solids, and report only observed API values."""

    workdir.mkdir(parents=True, exist_ok=True)
    modeler = None
    try:
        modeler = _launch(backend, workdir)
        details: dict[str, Any] = {
            "backend": backend,
            "backend_type": str(modeler.client.backend_type.name),
            "backend_version": str(modeler.client.backend_version),
            "api_proto": str(modeler.client.services.version.value),
            "package_version": version("ansys-geometry-core"),
            "healthy_before": bool(modeler.client.healthy),
        }
        if mode == "pmdb":
            from ansys.geometry.core.misc.options import PMDBExportOptions

            if source is None:
                raise ValueError("PMDB handoff export requires --source.")
            source = source.resolve(strict=True)
            _stage(f"opening:{source.name}")
            design = modeler.open_file(source)
            export_dir = (workdir / "pmdb").resolve()
            export_dir.mkdir(parents=True, exist_ok=True)
            _stage(f"exporting-pmdb:{source.name}")
            exported_path = Path(
                design.export_to_pmdb(
                    export_dir,
                    options=PMDBExportOptions(named_selection=True),
                )
            ).resolve()
            details["source"] = {
                "path": str(source),
                "size_bytes": source.stat().st_size,
            }
            details["asset"] = {
                "kind": asset,
                "path": str(exported_path),
                "exists": exported_path.is_file(),
                "size_bytes": exported_path.stat().st_size if exported_path.is_file() else 0,
            }
            _stage(f"exported-pmdb:{exported_path.name}")
        elif mode == "inventory":
            _stage(f"creating:{asset}")
            _, body = _create_box(modeler) if asset == "box" else _create_cylinder(modeler)
            _stage(f"inspecting:{asset}")
            details[asset] = _body_inventory(body)
            _stage(f"inspected:{asset}")
        elif mode == "save":
            _stage(f"creating:{asset}")
            design, _ = _create_box(modeler) if asset == "box" else _create_cylinder(modeler)
            asset_path = (workdir / f"g3-{asset}.scdocx").resolve()
            _stage(f"saving:{asset}")
            design.save(asset_path)
            details["asset"] = {
                "kind": asset,
                "path": str(asset_path),
                "exists": asset_path.is_file(),
                "size_bytes": asset_path.stat().st_size if asset_path.is_file() else 0,
            }
            _stage(f"saved:{asset}")
        else:
            _stage(f"creating:{asset}")
            _, body = _create_box(modeler) if asset == "box" else _create_cylinder(modeler)
            target = next(
                (
                    face
                    for face in body.faces
                    if asset == "box" or str(face.surface_type.name).endswith("CYLINDER")
                ),
                None,
            )
            if target is None:
                raise RuntimeError(f"No orientation probe face found for {asset}.")
            _stage(f"orientation:{asset}")
            details["orientation"] = {
                "asset": asset,
                "surface_type": str(target.surface_type.name),
                "result": _shape_attempt(target),
            }
        try:
            details["healthy_after"] = bool(modeler.client.healthy)
        except Exception as exc:
            details["healthy_after"] = False
            details["health_error"] = f"{type(exc).__name__}: {exc}"
        _stage("exiting")
    except Exception:
        if modeler is not None:
            _bounded_exit(modeler)
        raise
    details["client_exit"] = _bounded_exit(modeler)
    _stage("complete")
    return {"ok": True, "details": details}


def main(argv: list[str] | None = None) -> int:
    """Run the isolated probe and emit exactly one final JSON object."""

    parser = argparse.ArgumentParser()
    parser.add_argument("backend", choices=("discovery", "spaceclaim"))
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("inventory", "orientation", "save", "pmdb"),
        default="inventory",
    )
    parser.add_argument("--asset", choices=("box", "cylinder"), default="box")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = execute(
            args.backend,
            args.workdir.resolve(),
            args.mode,
            args.asset,
            args.source,
        )
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
