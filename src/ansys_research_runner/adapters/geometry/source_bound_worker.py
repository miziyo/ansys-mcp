"""Isolated Ansys SpaceClaim worker for source-bound exact geometry evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import traceback
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


def _floats(value: Any) -> list[float]:
    return [float(item) for item in value]


def _attempt_vector(getter: Any) -> list[float] | None:
    try:
        return _floats(getter())
    except Exception:
        return None


def _launch(workdir: Path) -> Any:
    # The installed v261 Korean resources are incomplete. Keep the reviewed child
    # process on the qualified locale without modifying the user's machine settings.
    os.environ["AWP_LOCALE261"] = "en-us"
    from ansys.geometry.core.connection.backend import ApiVersions
    from ansys.geometry.core.connection.launcher import launch_modeler_with_spaceclaim

    return launch_modeler_with_spaceclaim(
        version=261,
        api_version=ApiVersions.V_261,
        hidden=True,
        server_working_dir=workdir,
    )


def _bounded_exit(modeler: Any, timeout_seconds: float = 15.0) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    finished = threading.Event()

    def close() -> None:
        try:
            modeler.exit()
            outcome["completed"] = True
        except Exception as exc:
            outcome.update(
                completed=False,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        finally:
            finished.set()

    thread = threading.Thread(target=close, name="source-bound-geometry-exit", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if not finished.is_set():
        return {"completed": False, "reason": "CLIENT_EXIT_TIMEOUT"}
    return outcome


def _selection_memberships(design: Any) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    body_memberships: dict[str, set[str]] = {}
    face_memberships: dict[str, set[str]] = {}
    for selection in design.named_selections:
        name = str(selection.name)
        for body in selection.bodies:
            body_memberships.setdefault(str(body.id), set()).add(name)
        for face in selection.faces:
            face_memberships.setdefault(str(face.id), set()).add(name)
    return body_memberships, face_memberships


def _orientation(face: Any) -> dict[str, Any]:
    geometry = face.shape.geometry
    axis = getattr(geometry, "dir_z", None)
    return {
        "normal": _floats(face.normal()),
        "axis": None if axis is None else _floats(axis),
        "surface_class": type(geometry).__name__,
    }


def _inspect(source: Path, workdir: Path) -> dict[str, Any]:
    from ansys.geometry.core.misc.units import UNITS

    modeler = None
    try:
        _stage("launching:spaceclaim")
        modeler = _launch(workdir)
        _stage(f"opening:{source.name}")
        design = modeler.open_file(source)
        body_memberships, face_memberships = _selection_memberships(design)
        named_selections = sorted(str(selection.name) for selection in design.named_selections)
        bodies: list[dict[str, Any]] = []
        for body in design.get_all_bodies():
            if bool(body.is_surface):
                continue
            bounds = body.bounding_box
            faces: list[dict[str, Any]] = []
            for face in body.faces:
                face_bounds = face.bounding_box
                try:
                    orientation = _orientation(face)
                except Exception as exc:
                    raise RuntimeError(
                        f"Exact orientation unavailable for face {face.id}: {exc}"
                    ) from exc
                faces.append(
                    {
                        "runtime_id": str(face.id),
                        "surface_type": str(face.surface_type.name),
                        "area_m2": float(face.area.to(UNITS.m**2).magnitude),
                        "centroid_m": _attempt_vector(lambda face=face: face.centroid),
                        "bounding_box_min_m": _floats(face_bounds.min_corner),
                        "bounding_box_max_m": _floats(face_bounds.max_corner),
                        "normal": orientation["normal"],
                        "axis": orientation["axis"],
                        "surface_class": orientation["surface_class"],
                        "named_selections": sorted(face_memberships.get(str(face.id), set())),
                    }
                )
            bodies.append(
                {
                    "runtime_id": str(body.id),
                    "display_name": str(body.name),
                    "volume_m3": float(body.volume.to(UNITS.m**3).magnitude),
                    "surface_area_m2": sum(float(face["area_m2"]) for face in faces),
                    "centroid_m": _attempt_vector(lambda body=body: body.centroid),
                    "bounding_box_min_m": _floats(bounds.min_corner),
                    "bounding_box_max_m": _floats(bounds.max_corner),
                    "named_selections": sorted(body_memberships.get(str(body.id), set())),
                    "faces": faces,
                }
            )
        if len(bodies) != 1:
            raise RuntimeError(
                "source_bound_exact currently requires exactly one solid body; "
                f"observed {len(bodies)}."
            )
        details = {
            "backend": "pyansys_geometry_spaceclaim_source_bound",
            "backend_version": str(modeler.client.backend_version),
            "package_version": version("ansys-geometry-core"),
            "source_path": str(source),
            "source_sha256": _sha256(source),
            "named_selections": named_selections,
            "bodies": bodies,
            "healthy_after": bool(modeler.client.healthy),
        }
        _stage("exiting:spaceclaim")
    except Exception:
        if modeler is not None:
            _bounded_exit(modeler)
        raise
    details["client_exit"] = _bounded_exit(modeler)
    _stage("complete")
    return details


def _create_box_fixture(
    output: Path, workdir: Path, dimensions_m: tuple[float, float, float]
) -> dict[str, Any]:
    from ansys.geometry.core.math.point import Point2D
    from ansys.geometry.core.misc.units import UNITS
    from ansys.geometry.core.sketch.sketch import Sketch

    width, depth, height = dimensions_m
    if min(dimensions_m) <= 0.0:
        raise ValueError("Fixture dimensions must be positive.")
    modeler = None
    details: dict[str, Any]
    try:
        _stage("launching:spaceclaim")
        modeler = _launch(workdir)
        design = modeler.create_design("SourceBoundExactFixture")
        sketch = Sketch().box(
            Point2D([0.0, 0.0], unit=UNITS.m),
            width=width * UNITS.m,
            height=depth * UNITS.m,
        )
        body = design.extrude_sketch("ThermalDomain", sketch, height * UNITS.m)
        if body is None:
            raise RuntimeError("SpaceClaim returned no fixture body.")
        planar_x_faces = []
        for face in body.faces:
            bounds = face.bounding_box
            low = _floats(bounds.min_corner)
            high = _floats(bounds.max_corner)
            if abs(high[0] - low[0]) <= max(abs(low[0]), abs(high[0]), 1.0) * 1.0e-9:
                planar_x_faces.append((low[0], face))
        if len(planar_x_faces) != 2:
            raise RuntimeError("Fixture did not expose two X-normal end faces.")
        planar_x_faces.sort(key=lambda item: item[0])
        cold_face = planar_x_faces[0][1]
        hot_face = planar_x_faces[-1][1]
        design.create_named_selection("THERMAL_DOMAIN", bodies=[body])
        design.create_named_selection("COLD_FACE", faces=[cold_face])
        design.create_named_selection("HOT_FACE", faces=[hot_face])
        design.create_named_selection("EXTERIOR", faces=list(body.faces))
        output.parent.mkdir(parents=True, exist_ok=True)
        design.save(output)
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("SpaceClaim reported save success without an output file.")
        details = {
            "backend": "pyansys_geometry_spaceclaim_source_bound",
            "backend_version": str(modeler.client.backend_version),
            "package_version": version("ansys-geometry-core"),
            "asset_path": str(output),
            "asset_sha256": _sha256(output),
            "dimensions_m": list(dimensions_m),
            "named_selections": ["COLD_FACE", "EXTERIOR", "HOT_FACE", "THERMAL_DOMAIN"],
        }
        _stage("exiting:spaceclaim")
    except Exception:
        if modeler is not None:
            _bounded_exit(modeler)
        raise
    details["client_exit"] = _bounded_exit(modeler)
    _stage("complete")
    return details


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--create-box", type=Path)
    parser.add_argument("--dimensions-m", nargs=3, type=float, default=(1.0, 2.0, 3.0))
    args = parser.parse_args(argv)
    try:
        workdir = args.workdir.resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        if (args.source is None) == (args.create_box is None):
            raise ValueError("Specify exactly one of --source or --create-box.")
        if args.source is not None:
            source = args.source.resolve(strict=True)
            details = _inspect(source, workdir)
        else:
            assert args.create_box is not None
            dimension_values = tuple(float(item) for item in args.dimensions_m)
            if len(dimension_values) != 3:
                raise ValueError("Fixture dimensions must contain exactly three values.")
            dimensions_m = (
                dimension_values[0],
                dimension_values[1],
                dimension_values[2],
            )
            details = _create_box_fixture(
                args.create_box.resolve(),
                workdir,
                dimensions_m,
            )
        payload = {"ok": True, "details": details}
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
