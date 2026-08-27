"""Official Prime, MAPDL, and DPF thermal solver adapter."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import psutil

from ansys_research_runner.adapters.solver.base import (
    PostprocessResult,
    PreparedRun,
    RunCallbacks,
    SolverCapabilityReport,
    SolveResult,
)
from ansys_research_runner.domain.cae_ir import BackendTarget, ResolvedCAEIR
from ansys_research_runner.domain.errors import ErrorCode
from ansys_research_runner.domain.geometry import Vector3
from ansys_research_runner.domain.recipe import (
    ConvectionBoundary,
    CoordinateProbeOutput,
    HeatFluxBoundary,
    HotspotLocationOutput,
    PointUnit,
    TemperatureBoundary,
    TimeSeriesVolumetricHeatLoad,
    TotalHeatBoundary,
    VolumetricHeatLoad,
)
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    HotspotSummary,
    ProbeInterpolationStatus,
    ProbeResult,
    ScalarResultSummary,
    TemperatureSummary,
    ThermalObservation,
    TransientThermalObservation,
)
from ansys_research_runner.domain.units import PhysicalDimension, parse_quantity
from ansys_research_runner.domain.validation import ValidationIssue, ValidationReport
from ansys_research_runner.io import atomic_write_text
from ansys_research_runner.services.capability_service import resolve_ansys_root
from ansys_research_runner.services.contract_service import deterministic_json
from ansys_research_runner.services.field_service import (
    TemperatureFieldData,
    mesh_sha256,
    write_temperature_field,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _vector3(values: np.ndarray) -> Vector3:
    return Vector3((float(values[0]), float(values[1]), float(values[2])))


def _face_plane(evidence: Any) -> tuple[str, float] | None:
    bounds = evidence.measurements.get("bounding_box_m")
    if not isinstance(bounds, dict):
        return None
    minimum = bounds.get("minimum")
    maximum = bounds.get("maximum")
    if not isinstance(minimum, list) or not isinstance(maximum, list):
        return None
    if len(minimum) != 3 or len(maximum) != 3:
        return None
    spans = [abs(float(high) - float(low)) for low, high in zip(minimum, maximum, strict=True)]
    scale = max(max(abs(float(item)) for item in (*minimum, *maximum)), 1.0)
    planar = [index for index, span in enumerate(spans) if span <= scale * 1.0e-9]
    if len(planar) != 1:
        return None
    index = planar[0]
    return "XYZ"[index], 0.5 * (float(minimum[index]) + float(maximum[index]))


def _selector_contains_external(value: Any) -> bool:
    if isinstance(value, dict):
        if "external_of" in value:
            return True
        return any(_selector_contains_external(item) for item in value.values())
    if isinstance(value, list):
        return any(_selector_contains_external(item) for item in value)
    return False


def _axes_are_global(cae_ir: ResolvedCAEIR) -> bool:
    try:
        axes = cae_ir.coordinate_frame.axes()
    except Exception:
        return False
    expected = np.eye(3)
    actual = np.asarray([axis.root for axis in axes], dtype=np.float64)
    return bool(np.allclose(actual, expected, rtol=0.0, atol=1.0e-10))


class MapdlSolverAdapter:
    """Data-driven thermal adapter using only official installed Ansys components."""

    def __init__(
        self,
        ansys_root: Path | None = None,
        *,
        prime_timeout_s: float = 300.0,
        solve_timeout_s: float = 1200.0,
        n_processes: int = 2,
    ) -> None:
        self._ansys_root = (ansys_root or resolve_ansys_root()).resolve()
        self._mapdl = (self._ansys_root / "ansys" / "bin" / "winx64" / "ANSYS261.exe").resolve()
        self._prime_timeout_s = prime_timeout_s
        self._solve_timeout_s = solve_timeout_s
        self._n_processes = n_processes
        self._spawn_environment = os.environ.copy()
        self._closed = False
        self._active: subprocess.Popen[str] | None = None
        self._active_create_time: float | None = None
        self._lock = threading.Lock()

    def probe_capabilities(self) -> SolverCapabilityReport:
        """Report the exact executable and PyAnsys packages required by this backend."""

        prime_version = _package_version("ansys-meshing-prime")
        dpf_version = _package_version("ansys-dpf-core")
        available = (
            not self._closed
            and self._mapdl.is_file()
            and prime_version is not None
            and dpf_version is not None
        )
        missing: list[str] = []
        if self._closed:
            missing.append("ADAPTER_CLOSED")
        if not self._mapdl.is_file():
            missing.append("MAPDL_EXECUTABLE_MISSING")
        if prime_version is None:
            missing.append("ANSYS_MESHING_PRIME_MISSING")
        if dpf_version is None:
            missing.append("ANSYS_DPF_CORE_MISSING")
        return SolverCapabilityReport(
            backend="prime_mapdl_dpf",
            available=available,
            package_version=prime_version,
            product_version="26.1",
            launch_mode="mapdl_batch",
            capabilities=(
                "cad_import",
                "tetrahedral_mesh",
                "steady_thermal",
                "transient_thermal",
                "temperature_field",
                "dpf_postprocess",
            )
            if available
            else (),
            reason=None if available else ";".join(missing),
            evidence={
                "mapdl_executable": str(self._mapdl),
                "ansys_meshing_prime": prime_version,
                "ansys_dpf_core": dpf_version,
                "ansys_root": str(self._ansys_root),
            },
        )

    def prepare(self, cae_ir: ResolvedCAEIR, workdir: Path) -> PreparedRun:
        """Stage immutable CAE-IR in an isolated run directory."""

        if self._closed:
            raise RuntimeError("MAPDL solver adapter is closed.")
        resolved_workdir = workdir.resolve()
        resolved_workdir.mkdir(parents=True, exist_ok=True)
        solver_output = resolved_workdir / "solver-output"
        solver_output.mkdir(exist_ok=True)
        cae_ir_path = resolved_workdir / "cae_ir.json"
        atomic_write_text(cae_ir_path, deterministic_json(cae_ir) + "\n")
        return PreparedRun(
            run_id=cae_ir.run_id,
            workdir=resolved_workdir,
            cae_ir_path=cae_ir_path,
            source_model_path=Path(cae_ir.geometry.file).expanduser().resolve(),
            solver_output_dir=solver_output,
        )

    def precheck(self, prepared: PreparedRun) -> ValidationReport:
        """Reject unsupported or changed inputs before launching licensed processes."""

        issues: list[ValidationIssue] = []

        def add(code: str, path: str, message: str, **details: object) -> None:
            issues.append(ValidationIssue(code=code, path=path, message=message, details=details))

        try:
            cae_ir = ResolvedCAEIR.model_validate_json(
                prepared.cae_ir_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            add("CAE_IR_INVALID", "resolved/cae_ir.json", str(exc))
            return ValidationReport(valid=False, issues=tuple(issues))
        if cae_ir.backend_target is not BackendTarget.MAPDL:
            add(
                "BACKEND_TARGET_MISMATCH",
                "backend_target",
                "Prime/MAPDL adapter requires backend_target=mapdl.",
            )
        if not prepared.source_model_path.is_file():
            add(
                ErrorCode.SOURCE_MODEL_MISMATCH.value,
                "geometry.file",
                "Source geometry file does not exist.",
                source=str(prepared.source_model_path),
            )
        elif _sha256_file(prepared.source_model_path) != cae_ir.geometry.sha256:
            add(
                ErrorCode.SOURCE_MODEL_MISMATCH.value,
                "geometry.sha256",
                "Source geometry hash differs from the compiled CAE-IR.",
            )
        if prepared.source_model_path.suffix.lower() not in {
            ".step",
            ".stp",
            ".x_t",
            ".x_b",
            ".parasolid",
            ".scdoc",
            ".scdocx",
            ".dsco",
            ".pmdb",
        }:
            add(
                "CAD_FORMAT_UNSUPPORTED",
                "geometry.file",
                "v0 MAPDL backend requires a supported Prime CAD source.",
            )
        capability = self.probe_capabilities()
        if not capability.available:
            add(
                ErrorCode.SOLVER_CAPABILITY_MISSING.value,
                "backend.mapdl",
                capability.reason or "Prime/MAPDL/DPF capability unavailable.",
                **capability.evidence,
            )
        if len(cae_ir.resolved_bodies) != 1:
            add(
                "MAPDL_BODY_SCOPE_UNSUPPORTED",
                "resolved_bodies",
                "v0 MAPDL backend requires exactly one resolved thermal body.",
            )
        if len(cae_ir.materials) != 1:
            add(
                "MAPDL_MATERIAL_SCOPE_UNSUPPORTED",
                "materials",
                "v0 MAPDL backend requires one material assignment.",
            )
        if not _axes_are_global(cae_ir):
            add(
                "MAPDL_ROTATED_FRAME_UNSUPPORTED",
                "coordinate_frame",
                "v0 MAPDL boundary scoping requires axes aligned with CAD global coordinates.",
            )
        unsupported_loads = [
            item.type
            for item in (*cae_ir.loads, *cae_ir.boundary_conditions)
            if isinstance(item, (HeatFluxBoundary, TotalHeatBoundary))
        ]
        if unsupported_loads:
            add(
                "MAPDL_LOAD_UNSUPPORTED",
                "loads",
                "v0 MAPDL backend does not yet translate heat-flux or total-heat loads.",
                load_types=unsupported_loads,
            )
        for boundary in cae_ir.boundary_conditions:
            evidence = cae_ir.selection_evidence.get(boundary.region, ())
            if isinstance(boundary, TemperatureBoundary) and (
                len(evidence) != 1 or _face_plane(evidence[0]) is None
            ):
                add(
                    "MAPDL_TEMPERATURE_SCOPE_UNSUPPORTED",
                    f"boundary_conditions.{boundary.region}",
                    "Temperature scope must resolve to one global-axis planar face.",
                )
            if isinstance(boundary, ConvectionBoundary) and (
                not evidence
                or not all(_selector_contains_external(item.selector) for item in evidence)
            ):
                add(
                    "MAPDL_CONVECTION_SCOPE_UNSUPPORTED",
                    f"boundary_conditions.{boundary.region}",
                    "Convection scope must be the resolved exterior of the thermal body.",
                )
        if sum(isinstance(item, ConvectionBoundary) for item in cae_ir.boundary_conditions) > 1:
            add(
                "MAPDL_CONVECTION_COUNT_UNSUPPORTED",
                "boundary_conditions",
                "v0 MAPDL backend supports one exterior convection condition.",
            )
        if cae_ir.analysis_settings.type == "transient":
            material = next(iter(cae_ir.materials.values()), None)
            if material is None or material.density is None or material.specific_heat is None:
                add(
                    "TRANSIENT_MATERIAL_INCOMPLETE",
                    "materials",
                    "Transient MAPDL solve requires density and specific heat.",
                )
        return ValidationReport(valid=not issues, issues=tuple(issues))

    def _set_active(self, process: subprocess.Popen[str] | None) -> None:
        with self._lock:
            self._active = process
            self._active_create_time = (
                psutil.Process(process.pid).create_time() if process is not None else None
            )

    def _run_process(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        log_path: Path,
        timeout_s: float,
        callbacks: RunCallbacks,
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8", errors="replace") as output:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                env=self._spawn_environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._set_active(process)
            started = time.monotonic()
            try:
                while process.poll() is None:
                    callbacks.heartbeat()
                    if time.monotonic() - started > timeout_s:
                        self._terminate_exact_process(process.pid, self._active_create_time)
                        raise TimeoutError(f"Process exceeded {timeout_s:.17g} seconds")
                    time.sleep(0.25)
                return int(process.returncode or 0)
            finally:
                self._set_active(None)

    @staticmethod
    def _element_size_mm(cae_ir: ResolvedCAEIR) -> float:
        selected = cae_ir.mesh_policy.characteristic_length.si_value
        if cae_ir.mesh_policy.maximum_size is not None:
            selected = min(selected, cae_ir.mesh_policy.maximum_size.si_value)
        if cae_ir.mesh_policy.minimum_size is not None:
            selected = max(selected, cae_ir.mesh_policy.minimum_size.si_value)
        return selected * 1000.0

    def _prime_mesh(
        self,
        prepared: PreparedRun,
        cae_ir: ResolvedCAEIR,
        callbacks: RunCallbacks,
    ) -> tuple[Path, dict[str, Any]]:
        cdb_path = prepared.solver_output_dir / "model.cdb"
        log_path = prepared.solver_output_dir / "prime.log"
        arguments = [
            sys.executable,
            "-m",
            "ansys_research_runner.adapters.geometry.prime_cdb_worker",
            "--asset",
            str(prepared.source_model_path),
            "--output",
            str(cdb_path),
            "--element-size-mm",
            f"{self._element_size_mm(cae_ir):.17g}",
        ]
        callbacks.log("Prime CAD import and volume mesh started")
        return_code = self._run_process(
            arguments,
            cwd=prepared.workdir,
            log_path=log_path,
            timeout_s=self._prime_timeout_s,
            callbacks=callbacks,
        )
        lines = [line.strip() for line in log_path.read_text(encoding="utf-8").splitlines()]
        payload: dict[str, Any] = {}
        for line in reversed(lines):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "ok" in candidate:
                payload = candidate
                break
        if return_code != 0 or payload.get("ok") is not True or not cdb_path.is_file():
            raise RuntimeError(f"Prime mesh failed; see {log_path}")
        details = payload.get("details")
        if not isinstance(details, dict):
            raise RuntimeError("Prime worker omitted mesh details")
        count = sum(
            int(item.get("cells", 0)) for item in details.get("parts", []) if isinstance(item, dict)
        )
        maximum = cae_ir.mesh_policy.maximum_elements
        if maximum is not None and count > maximum:
            raise RuntimeError(f"Prime mesh has {count} elements; limit is {maximum}")
        return cdb_path, details

    @staticmethod
    def _material_commands(cae_ir: ResolvedCAEIR) -> list[str]:
        material = next(iter(cae_ir.materials.values()))
        conductivity = material.thermal_conductivity.si_value / 1000.0
        commands = [
            "ET,2,278",
            f"MP,KXX,2,{conductivity:.17g}",
            f"MP,KYY,2,{conductivity:.17g}",
            f"MP,KZZ,2,{conductivity:.17g}",
        ]
        if material.density is not None:
            commands.append(f"MP,DENS,2,{material.density.si_value / 1.0e9:.17g}")
        if material.specific_heat is not None:
            commands.append(f"MP,C,2,{material.specific_heat.si_value:.17g}")
        return commands

    @staticmethod
    def _temperature_scope_commands(cae_ir: ResolvedCAEIR) -> list[str]:
        commands: list[str] = []
        for boundary in cae_ir.boundary_conditions:
            if not isinstance(boundary, TemperatureBoundary):
                continue
            evidence = cae_ir.selection_evidence[boundary.region][0]
            plane = _face_plane(evidence)
            if plane is None:
                raise RuntimeError(f"Unsupported temperature face {boundary.region}")
            axis, coordinate_m = plane
            commands.extend(
                [
                    "ALLSEL,ALL",
                    f"NSEL,S,LOC,{axis},{coordinate_m * 1000.0:.17g}",
                    f"D,ALL,TEMP,{boundary.value.si_value:.17g}",
                ]
            )
        commands.append("ALLSEL,ALL")
        return commands

    @staticmethod
    def _load_commands(cae_ir: ResolvedCAEIR) -> list[str]:
        commands: list[str] = []
        for load in cae_ir.loads:
            if isinstance(load, VolumetricHeatLoad):
                commands.extend(
                    [
                        "ESEL,S,TYPE,,2",
                        f"BFE,ALL,HGEN,,{load.value.si_value / 1.0e9:.17g}",
                    ]
                )
            elif isinstance(load, TimeSeriesVolumetricHeatLoad):
                profile = cae_ir.resolved_time_profiles[load.region]
                count = len(profile.points)
                times = ",".join(f"{item.time_s:.17g}" for item in profile.points)
                values = ",".join(
                    f"{item.heat_generation_W_m3 / 1.0e9:.17g}" for item in profile.points
                )
                commands.extend(
                    [
                        f"*DIM,QPROF,TABLE,{count},1,,TIME",
                        f"QPROF(1,0)={times}",
                        f"QPROF(1,1)={values}",
                        "ESEL,S,TYPE,,2",
                        "BFE,ALL,HGEN,,%QPROF%",
                    ]
                )
        return commands

    @staticmethod
    def _convection_commands(cae_ir: ResolvedCAEIR) -> list[str]:
        convection = next(
            (item for item in cae_ir.boundary_conditions if isinstance(item, ConvectionBoundary)),
            None,
        )
        if convection is None:
            return []
        return [
            "ESEL,S,TYPE,,2",
            "NSLE,S",
            "ET,10,152",
            "KEYOPT,10,5,0",
            "KEYOPT,10,8,2",
            "TYPE,10",
            "MAT,2",
            "REAL,10",
            "ESURF",
            "ESEL,S,TYPE,,10",
            f"SFE,ALL,1,CONV,0,{convection.film_coefficient.si_value / 1.0e6:.17g}",
            f"SFE,ALL,1,CONV,2,{convection.ambient_temperature.si_value:.17g}",
            "ALLSEL,ALL",
        ]

    @staticmethod
    def _metric_commands(cae_ir: ResolvedCAEIR) -> list[str]:
        commands = ["/POST1", "SET,LAST", "*CFOPEN,metrics,txt"]
        for index, boundary in enumerate(cae_ir.boundary_conditions):
            if not isinstance(boundary, TemperatureBoundary):
                continue
            plane = _face_plane(cae_ir.selection_evidence[boundary.region][0])
            if plane is None:
                continue
            axis, coordinate_m = plane
            commands.extend(
                [
                    "ALLSEL,ALL",
                    f"NSEL,S,LOC,{axis},{coordinate_m * 1000.0:.17g}",
                    "FSUM",
                    f"*GET,QB{index},FSUM,,ITEM,HEAT",
                    f"*VWRITE,QB{index}",
                    f"('BOUNDARY_{index} ',E20.12)",
                ]
            )
        if any(isinstance(item, ConvectionBoundary) for item in cae_ir.boundary_conditions):
            commands.extend(
                [
                    "ALLSEL,ALL",
                    "ESEL,S,TYPE,,10",
                    "NSLE,S",
                    "FSUM",
                    "*GET,QCONV,FSUM,,ITEM,HEAT",
                    "*VWRITE,QCONV",
                    "('CONVECTION ',E20.12)",
                ]
            )
        commands.extend(["*CFCLOS", "ALLSEL,ALL", "FINISH"])
        return commands

    def _write_input(self, cae_ir: ResolvedCAEIR, cdb_path: Path, output: Path) -> None:
        cdb_stem = str(cdb_path.with_suffix(""))
        commands = [
            "/BATCH",
            "/CLEAR,NOSTART",
            f"/INPUT,'{cdb_stem}','cdb'",
            "/PREP7",
            *self._material_commands(cae_ir),
            *self._load_commands(cae_ir),
        ]
        initial = cae_ir.analysis_settings.initial_temperature
        if initial is not None:
            commands.extend(["ESEL,S,TYPE,,2", "NSLE,S", f"IC,ALL,TEMP,{initial.si_value:.17g}"])
        commands.extend(self._convection_commands(cae_ir))
        commands.extend(self._temperature_scope_commands(cae_ir))
        commands.extend(["FINISH", "/SOLU"])
        if cae_ir.analysis_settings.type == "steady":
            commands.extend(["ANTYPE,STATIC", "OUTRES,ALL,ALL", "SOLVE", "FINISH"])
            commands.extend(self._metric_commands(cae_ir))
        else:
            end = cae_ir.analysis_settings.end_time
            step = cae_ir.analysis_settings.time_step
            assert end is not None and step is not None
            commands.extend(
                [
                    "ANTYPE,TRANS",
                    "TRNOPT,FULL",
                    "KBC,1",
                    "AUTOTS,OFF",
                    f"TIME,{end.si_value:.17g}",
                    f"DELTIM,{step.si_value:.17g},{step.si_value:.17g},{step.si_value:.17g}",
                    "OUTRES,ALL,ALL",
                    "SOLVE",
                    "FINISH",
                ]
            )
        commands.extend(["/EXIT,NOSAVE", ""])
        atomic_write_text(output, "\n".join(commands))

    def solve(self, prepared: PreparedRun, callbacks: RunCallbacks) -> SolveResult:
        """Mesh the source CAD, compile reviewed APDL, and run MAPDL batch."""

        started_at = _utc_now()
        precheck = self.precheck(prepared)
        if not precheck.valid:
            capability_missing = any(
                item.code == ErrorCode.SOLVER_CAPABILITY_MISSING.value for item in precheck.issues
            )
            return SolveResult(
                run_id=prepared.run_id,
                status=(
                    ExecutionStatus.BLOCKED_ENVIRONMENT
                    if capability_missing
                    else ExecutionStatus.FAILED_PRECHECK
                ),
                converged=None,
                started_at=started_at,
                finished_at=_utc_now(),
                message="; ".join(item.message for item in precheck.issues),
                evidence={"issues": [item.model_dump(mode="json") for item in precheck.issues]},
            )
        cae_ir = ResolvedCAEIR.model_validate_json(prepared.cae_ir_path.read_text(encoding="utf-8"))
        try:
            cdb_path, prime_details = self._prime_mesh(prepared, cae_ir, callbacks)
            input_path = prepared.solver_output_dir / "thermal.inp"
            self._write_input(cae_ir, cdb_path, input_path)
            output_path = prepared.solver_output_dir / "thermal.out"
            callbacks.log("MAPDL thermal solve started")
            arguments = [
                str(self._mapdl),
                "-b",
                "-p",
                "ansys",
                "-np",
                str(self._n_processes),
                "-dir",
                str(prepared.solver_output_dir),
                "-j",
                "thermal",
                "-s",
                "read",
                "-l",
                "en-us",
                "-i",
                str(input_path),
                "-o",
                str(output_path),
            ]
            console_path = prepared.solver_output_dir / "mapdl-console.log"
            exit_code = self._run_process(
                arguments,
                cwd=prepared.solver_output_dir,
                log_path=console_path,
                timeout_s=self._solve_timeout_s,
                callbacks=callbacks,
            )
            result_path = prepared.solver_output_dir / "thermal.rst"
            output_text = (
                output_path.read_text(encoding="utf-8", errors="replace")
                if output_path.is_file()
                else ""
            )
            error_match = re.search(r"NUMBER OF ERROR\s+MESSAGES ENCOUNTERED=\s*(\d+)", output_text)
            error_count = int(error_match.group(1)) if error_match else None
            succeeded = (
                exit_code == 0
                and result_path.is_file()
                and "RUN COMPLETED" in output_text
                and error_count == 0
            )
            result_files = {
                "cdb": cdb_path,
                "input": input_path,
                "solver_output": output_path,
                "result": result_path,
                "prime_log": prepared.solver_output_dir / "prime.log",
                "mapdl_console": console_path,
            }
            metrics = prepared.solver_output_dir / "metrics.txt"
            if metrics.is_file():
                result_files["metrics"] = metrics
            return SolveResult(
                run_id=prepared.run_id,
                status=(ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED_SOLVER),
                converged=bool(succeeded),
                started_at=started_at,
                finished_at=_utc_now(),
                exit_code=exit_code,
                message=(
                    "Prime mesh and MAPDL solve completed"
                    if succeeded
                    else "MAPDL did not produce an error-free completed result"
                ),
                result_files=result_files,
                evidence={
                    "prime": prime_details,
                    "mapdl_error_count": error_count,
                    "owned_process_remaining": [],
                },
            )
        except TimeoutError as exc:
            return SolveResult(
                run_id=prepared.run_id,
                status=ExecutionStatus.FAILED_SOLVER,
                converged=False,
                started_at=started_at,
                finished_at=_utc_now(),
                message=str(exc),
                evidence={"owned_process_remaining": []},
            )
        except Exception as exc:
            return SolveResult(
                run_id=prepared.run_id,
                status=ExecutionStatus.FAILED_SOLVER,
                converged=False,
                started_at=started_at,
                finished_at=_utc_now(),
                message=str(exc),
                evidence={
                    "error_type": type(exc).__name__,
                    "owned_process_remaining": [],
                },
            )

    @staticmethod
    def _mesh_arrays(model: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        mesh = model.metadata.meshed_region
        node_ids = np.asarray(mesh.nodes.scoping.ids, dtype=np.int64)
        coordinates_m = np.asarray(mesh.nodes.coordinates_field.data, dtype=np.float64) * 1.0e-3
        element_ids = np.asarray(mesh.elements.scoping.ids, dtype=np.int64)
        connectivities = [
            np.asarray(mesh.elements.connectivities_field.get_entity_data(index), dtype=np.int64)
            for index in range(element_ids.size)
        ]
        widths = {item.size for item in connectivities}
        if len(widths) != 1:
            raise RuntimeError("Mixed-width MAPDL connectivity cannot use field schema v1")
        connectivity = np.stack([node_ids[item] for item in connectivities]).astype(np.int64)
        return node_ids, coordinates_m, element_ids, connectivity

    @staticmethod
    def _ordered_temperatures(fields: Any, node_ids: np.ndarray) -> np.ndarray:
        frames: list[np.ndarray] = []
        for field in fields:
            values = {
                int(node): float(value)
                for node, value in zip(field.scoping.ids, field.data, strict=True)
            }
            frames.append(np.asarray([values[int(node)] for node in node_ids], dtype=np.float64))
        return np.stack(frames)

    @staticmethod
    def _volume_averages(model: Any, fields: Any) -> tuple[float, ...]:
        dpf = __import__("ansys.dpf.core", fromlist=["core"])
        elemental = dpf.operators.averaging.nodal_to_elemental_fc(
            fields_container=fields,
            mesh=model.metadata.meshed_region,
        ).outputs.fields_container()
        volumes = model.results.elemental_volume.eval()[0]
        volume_by_id = {
            int(identifier): float(value)
            for identifier, value in zip(
                volumes.scoping.ids,
                np.asarray(volumes.data).reshape(-1),
                strict=True,
            )
        }
        averages: list[float] = []
        for field in elemental:
            weighted = [
                (float(value), volume_by_id.get(int(identifier), 0.0))
                for identifier, value in zip(field.scoping.ids, field.data, strict=True)
            ]
            volume = sum(weight for _, weight in weighted)
            averages.append(sum(value * weight for value, weight in weighted) / volume)
        return tuple(averages)

    @staticmethod
    def _requested_point(
        output: CoordinateProbeOutput, coordinates_m: np.ndarray, cae_ir: ResolvedCAEIR
    ) -> np.ndarray:
        point = np.asarray(output.point.root, dtype=np.float64)
        if output.point_unit is PointUnit.NORMALIZED_MODEL_COORDINATES:
            minimum = np.min(coordinates_m, axis=0)
            maximum = np.max(coordinates_m, axis=0)
            return np.asarray(minimum + point * (maximum - minimum), dtype=np.float64)
        factor = parse_quantity(
            f"1 {cae_ir.geometry.length_unit}", PhysicalDimension.LENGTH
        ).si_value
        return np.asarray(point * factor, dtype=np.float64)

    @staticmethod
    def _interpolate_tetra(
        point: np.ndarray,
        node_ids: np.ndarray,
        coordinates_m: np.ndarray,
        connectivity: np.ndarray,
        temperatures: np.ndarray,
    ) -> tuple[np.ndarray, float] | None:
        index = {int(identifier): position for position, identifier in enumerate(node_ids)}
        for row in connectivity:
            positions = np.asarray([index[int(identifier)] for identifier in row], dtype=np.int64)
            vertices = coordinates_m[positions]
            if np.any(point < np.min(vertices, axis=0) - 1.0e-10) or np.any(
                point > np.max(vertices, axis=0) + 1.0e-10
            ):
                continue
            matrix = np.column_stack(
                (vertices[1] - vertices[0], vertices[2] - vertices[0], vertices[3] - vertices[0])
            )
            try:
                tail = np.linalg.solve(matrix, point - vertices[0])
            except np.linalg.LinAlgError:
                continue
            weights = np.asarray([1.0 - np.sum(tail), *tail], dtype=np.float64)
            if np.all(weights >= -1.0e-8) and np.all(weights <= 1.0 + 1.0e-8):
                return point, float(np.dot(weights, temperatures[positions]))
        return None

    @staticmethod
    def _boundary_temperature(
        evidence: Any,
        coordinates_m: np.ndarray,
        temperatures: np.ndarray,
    ) -> float:
        bounds = evidence.measurements["bounding_box_m"]
        minimum = np.asarray(bounds["minimum"], dtype=np.float64)
        maximum = np.asarray(bounds["maximum"], dtype=np.float64)
        scale = max(float(np.max(maximum - minimum)), 1.0)
        mask = np.all(
            (coordinates_m >= minimum - scale * 1.0e-8)
            & (coordinates_m <= maximum + scale * 1.0e-8),
            axis=1,
        )
        if not np.any(mask):
            raise RuntimeError(f"No result nodes map to face {evidence.stable_key}")
        return float(np.mean(temperatures[mask]))

    @staticmethod
    def _metrics(path: Path) -> dict[str, float]:
        if not path.is_file():
            return {}
        metrics: dict[str, float] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s*([A-Z_0-9]+)\s+([-+0-9.Ee]+)\s*$", line)
            if match:
                metrics[match.group(1)] = float(match.group(2))
        return metrics

    def postprocess(
        self,
        prepared: PreparedRun,
        solve_result: SolveResult,
    ) -> PostprocessResult:
        """Extract native solver results with DPF and emit the strict HDF5 field."""

        if solve_result.status is not ExecutionStatus.SUCCEEDED:
            raise RuntimeError(f"Cannot postprocess {solve_result.status.value}")
        dpf = __import__("ansys.dpf.core", fromlist=["core"])
        cae_ir = ResolvedCAEIR.model_validate_json(prepared.cae_ir_path.read_text(encoding="utf-8"))
        result_path = solve_result.result_files["result"]
        model = dpf.Model(str(result_path))
        fields = model.results.temperature(time_scoping=-1).eval()
        node_ids, coordinates_m, element_ids, connectivity = self._mesh_arrays(model)
        temperature = self._ordered_temperatures(fields, node_ids)
        times = np.asarray(model.metadata.time_freq_support.time_frequencies.data, dtype=np.float64)
        averages = self._volume_averages(model, fields)
        if cae_ir.analysis_settings.type == "transient":
            initial = cae_ir.analysis_settings.initial_temperature
            assert initial is not None
            if times.size == 0 or not math.isclose(float(times[0]), 0.0, abs_tol=1.0e-12):
                times = np.concatenate((np.asarray([0.0]), times))
                temperature = np.vstack(
                    (np.full((1, node_ids.size), initial.si_value), temperature)
                )
                averages = (initial.si_value, *averages)
        digest = mesh_sha256(node_ids, coordinates_m, element_ids, connectivity)
        field_path = prepared.solver_output_dir / "temperature_field.h5"
        field_report = write_temperature_field(
            field_path,
            TemperatureFieldData(
                node_ids=node_ids,
                coordinates_m=coordinates_m,
                element_ids=element_ids,
                connectivity=connectivity,
                times_s=times,
                temperature_K=temperature,
                mesh_sha256=digest,
            ),
        )
        if not field_report.valid:
            raise RuntimeError("DPF field failed HDF5 integrity validation")
        last = temperature[-1]
        maximum_index = int(np.argmax(last))
        summary = ScalarResultSummary(
            temperature=TemperatureSummary(
                minimum_K=float(np.min(temperature)),
                maximum_K=float(np.max(temperature)),
                volume_average_K=float(averages[-1]),
            ),
            hotspot=(
                HotspotSummary(
                    position_m=_vector3(coordinates_m[maximum_index]),
                    value_K=float(last[maximum_index]),
                )
                if any(isinstance(item, HotspotLocationOutput) for item in cae_ir.requested_outputs)
                or cae_ir.analysis_settings.type == "steady"
                else None
            ),
        )
        probes: list[ProbeResult] = []
        for output in cae_ir.requested_outputs:
            if not isinstance(output, CoordinateProbeOutput):
                continue
            requested = self._requested_point(output, coordinates_m, cae_ir)
            mapped = self._interpolate_tetra(requested, node_ids, coordinates_m, connectivity, last)
            if mapped is None:
                probes.append(
                    ProbeResult(
                        name=output.name,
                        requested_position_m=_vector3(requested),
                        coordinate_system=output.point_unit.value,
                        inside_mesh=False,
                        interpolation_status=ProbeInterpolationStatus.OUTSIDE_MESH,
                    )
                )
            else:
                mapped_point, value = mapped
                probes.append(
                    ProbeResult(
                        name=output.name,
                        requested_position_m=_vector3(requested),
                        coordinate_system=output.point_unit.value,
                        mapped_position_m=_vector3(mapped_point),
                        inside_mesh=True,
                        interpolation_status=ProbeInterpolationStatus.INTERPOLATED,
                        value_K=value,
                    )
                )
        if cae_ir.analysis_settings.type == "transient":
            observation: ThermalObservation | TransientThermalObservation = (
                TransientThermalObservation(
                    summary=summary,
                    times_s=tuple(float(item) for item in times),
                    volume_average_temperature_K=tuple(float(item) for item in averages),
                    probes=tuple(probes),
                )
            )
        else:
            boundary_temperatures = {
                boundary.region: self._boundary_temperature(
                    cae_ir.selection_evidence[boundary.region][0], coordinates_m, last
                )
                for boundary in cae_ir.boundary_conditions
                if isinstance(boundary, TemperatureBoundary)
            }
            metrics = self._metrics(prepared.solver_output_dir / "metrics.txt")
            reactions = [
                abs(metrics.get(f"BOUNDARY_{index}", 0.0))
                for index, boundary in enumerate(cae_ir.boundary_conditions)
                if isinstance(boundary, TemperatureBoundary)
            ]
            generation = next(
                (
                    item.value.si_value
                    * sum(
                        float(value)
                        for value in np.asarray(
                            model.results.elemental_volume.eval()[0].data
                        ).reshape(-1)
                    )
                    / 1.0e9
                    for item in cae_ir.loads
                    if isinstance(item, VolumetricHeatLoad)
                ),
                None,
            )
            observation = ThermalObservation(
                summary=summary,
                probes=tuple(probes),
                boundary_temperatures_K=boundary_temperatures,
                heat_input_W=reactions[0] if reactions else None,
                heat_output_W=reactions[1] if len(reactions) > 1 else None,
                heat_generation_W=generation,
                heat_rejection_W=(abs(metrics["CONVECTION"]) if "CONVECTION" in metrics else None),
            )
        artifacts = {key: path for key, path in solve_result.result_files.items() if path.is_file()}
        return PostprocessResult(
            observation=observation,
            field_path=field_path,
            mesh_sha256=digest,
            artifacts=artifacts,
        )

    @staticmethod
    def _terminate_exact_process(pid: int, create_time: float | None) -> None:
        if create_time is None:
            return
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - create_time) > 0.01:
                return
            children = process.children(recursive=True)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            return
        for child in reversed(children):
            with suppress(psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                child.terminate()
        with suppress(psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            process.terminate()
        _, alive = psutil.wait_procs([*children, process], timeout=2.0)
        for survivor in alive:
            with suppress(psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                survivor.kill()

    def request_cancel(self, prepared: PreparedRun) -> None:
        """Cancel only the exact run-owned process tree and persist a marker."""

        atomic_write_text(prepared.workdir / "cancel.requested", prepared.run_id + "\n")
        with self._lock:
            active = self._active
            create_time = self._active_create_time
        if active is not None:
            self._terminate_exact_process(active.pid, create_time)

    def close(self) -> None:
        """Release any exact active process and disable future launches."""

        with self._lock:
            active = self._active
            create_time = self._active_create_time
        if active is not None:
            self._terminate_exact_process(active.pid, create_time)
        self._closed = True
