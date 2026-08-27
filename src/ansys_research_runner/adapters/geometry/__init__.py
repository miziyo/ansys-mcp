"""Geometry adapter contracts and implementations."""

from ansys_research_runner.adapters.geometry.pyansys_geometry import PyAnsysGeometryAdapter
from ansys_research_runner.adapters.geometry.synthetic import SyntheticGeometryAdapter

__all__ = ["PyAnsysGeometryAdapter", "SyntheticGeometryAdapter"]
