"""Solver adapter contracts and official backend implementations."""

from ansys_research_runner.adapters.solver.base import SolverAdapter
from ansys_research_runner.adapters.solver.mapdl import MapdlSolverAdapter
from ansys_research_runner.adapters.solver.pymechanical import PyMechanicalSolverAdapter

__all__ = ["MapdlSolverAdapter", "PyMechanicalSolverAdapter", "SolverAdapter"]
