"""Parametric, non-proprietary box fixture for the G3 live geometry gate."""

from typing import Any

WIDTH_MM = 2000.0
DEPTH_MM = 3000.0
HEIGHT_MM = 4000.0


def gen_step() -> Any:
    """Return one labeled box solid centered on XY with its base at Z=0."""

    from build123d import Align, Box  # type: ignore[import-not-found]

    box = Box(
        WIDTH_MM,
        DEPTH_MM,
        HEIGHT_MM,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    box.label = "g3_box"
    return box
