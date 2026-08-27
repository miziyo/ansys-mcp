"""Safe loading and validation of transient volumetric heat-generation CSV profiles."""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

from ansys_research_runner.domain.transient import (
    HeatGenerationPoint,
    ResolvedHeatGenerationProfile,
)

_HEADERS = ("time_s", "heat_generation_W_m3")


def load_heat_generation_profile(
    path: Path,
    *,
    expected_end_time_s: float,
    maximum_bytes: int = 1_048_576,
) -> ResolvedHeatGenerationProfile:
    """Load a bounded exact-schema CSV and require it to span the requested analysis time."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    if resolved.stat().st_size > maximum_bytes:
        raise ValueError(f"Heat-generation CSV exceeds {maximum_bytes} bytes.")
    raw = resolved.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Heat-generation CSV must be UTF-8.") from exc
    reader = csv.DictReader(text.splitlines())
    if tuple(reader.fieldnames or ()) != _HEADERS:
        raise ValueError(f"Heat-generation CSV headers must be exactly {_HEADERS!r}.")
    points: list[HeatGenerationPoint] = []
    for index, row in enumerate(reader, start=2):
        if set(row) != set(_HEADERS) or any(row[name] is None for name in _HEADERS):
            raise ValueError(f"Invalid heat-generation CSV row {index}.")
        try:
            point = HeatGenerationPoint(
                time_s=float(row["time_s"]),
                heat_generation_W_m3=float(row["heat_generation_W_m3"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid numeric value in heat-generation CSV row {index}.") from exc
        points.append(point)
    profile = ResolvedHeatGenerationProfile(
        source_file=str(resolved),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        points=tuple(points),
    )
    tolerance = max(1.0e-9, abs(expected_end_time_s) * 1.0e-12)
    if not math.isclose(profile.points[0].time_s, 0.0, abs_tol=tolerance):
        raise ValueError("Heat-generation profile must begin at 0 s.")
    if not math.isclose(
        profile.points[-1].time_s,
        expected_end_time_s,
        rel_tol=0.0,
        abs_tol=tolerance,
    ):
        raise ValueError("Heat-generation profile end time must match analysis.end_time.")
    return profile
