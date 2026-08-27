"""Parametric, non-proprietary cylinder fixture for the G3 live geometry gate."""

from typing import Any

RADIUS_MM = 500.0
HEIGHT_MM = 2000.0


def gen_step() -> Any:
    """Return one labeled cylinder centered on XY with its base at Z=0."""

    from build123d import Align, Cylinder  # type: ignore[import-not-found]

    cylinder = Cylinder(
        RADIUS_MM,
        HEIGHT_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    cylinder.label = "g3_cylinder"
    return cylinder
