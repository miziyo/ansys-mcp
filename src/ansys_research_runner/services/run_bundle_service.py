"""Atomic, path-confined creation of the required run bundle layout."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import platform
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.cae_ir import ResolvedCAEIR
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.recipe import ModelManifest, RunRecipe
from ansys_research_runner.domain.results import (
    ExecutionStatus,
    PhysicalVerificationReport,
    ProbeResult,
    ResultQualitySummary,
    ScalarResultSummary,
    VerificationStatus,
)
from ansys_research_runner.domain.run_bundle import (
    ArtifactDigest,
    ArtifactKind,
    RunBundleManifest,
    RunBundlePhase,
    RunBundleState,
)
from ansys_research_runner.domain.selectors import RegionResolution
from ansys_research_runner.io import atomic_write_bytes, atomic_write_json, atomic_write_text
from ansys_research_runner.services.contract_service import deterministic_json


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


@dataclass(frozen=True, slots=True)
class RunBundlePaths:
    """Resolved directories for one path-confined run."""

    root: Path
    request: Path
    resolved: Path
    work: Path
    results: Path
    artifacts: Path
    logs: Path


class RunBundleService:
    """Create and finalize deterministic run evidence without modifying source CAD."""

    def __init__(self, runs_root: Path | None = None) -> None:
        self._runs_root = (runs_root or RunnerPaths.from_environment().runs).resolve()
        self._runs_root.mkdir(parents=True, exist_ok=True)

    def create(self, run_id: str) -> RunBundlePaths:
        """Create the required directory tree for a new run ID."""

        state = RunBundleState(
            run_id=run_id,
            phase=RunBundlePhase.CREATED,
            updated_at=_utc_now(),
        )
        root = (self._runs_root / run_id).resolve()
        if not root.is_relative_to(self._runs_root):
            raise ValueError("Run ID escapes the configured runs root.")
        if root.exists() and any(root.iterdir()):
            raise FileExistsError(f"Run bundle already exists: {run_id}")
        paths = RunBundlePaths(
            root=root,
            request=root / "request",
            resolved=root / "resolved",
            work=root / "work",
            results=root / "results",
            artifacts=root / "artifacts",
            logs=root / "logs",
        )
        for directory in (
            paths.request,
            paths.resolved,
            paths.work,
            paths.results,
            paths.artifacts,
            paths.logs,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.write_state(paths, state)
        return paths

    def write_state(self, paths: RunBundlePaths, state: RunBundleState) -> None:
        """Atomically replace the small run state snapshot."""

        if state.run_id != paths.root.name:
            raise ValueError("State run_id differs from its bundle directory.")
        atomic_write_text(paths.root / "state.json", deterministic_json(state) + "\n")

    def stage_request(
        self,
        paths: RunBundlePaths,
        *,
        recipe: RunRecipe,
        manifest: ModelManifest,
        auxiliary_files: dict[str, Path] | None = None,
    ) -> dict[str, str]:
        """Persist safe YAML inputs and their exact hashes."""

        recipe_text = yaml.safe_dump(
            recipe.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            allow_unicode=True,
        )
        manifest_text = yaml.safe_dump(
            manifest.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            allow_unicode=True,
        )
        recipe_path = paths.request / "recipe.yaml"
        manifest_path = paths.request / "model_manifest.yaml"
        atomic_write_text(recipe_path, recipe_text)
        atomic_write_text(manifest_path, manifest_text)
        hashes = {
            "recipe.yaml": _sha256_path(recipe_path),
            "model_manifest.yaml": _sha256_path(manifest_path),
        }
        for filename, source in sorted((auxiliary_files or {}).items()):
            if Path(filename).name != filename:
                raise ValueError("Auxiliary input filename must be a plain basename.")
            payload = source.resolve().read_bytes()
            destination = paths.request / filename
            atomic_write_bytes(destination, payload)
            hashes[filename] = _sha256_bytes(payload)
        atomic_write_json(paths.request / "input_hashes.json", hashes)
        self.write_state(
            paths,
            RunBundleState(
                run_id=paths.root.name,
                phase=RunBundlePhase.REQUEST_STAGED,
                updated_at=_utc_now(),
            ),
        )
        return hashes

    def write_resolved(
        self,
        paths: RunBundlePaths,
        *,
        cae_ir: ResolvedCAEIR,
        graph: GeometryGraph,
        resolution: RegionResolution,
    ) -> None:
        """Persist all solver-bound and semantic-resolution evidence."""

        documents: dict[str, BaseModel] = {
            "cae_ir.json": cae_ir,
            "geometry_graph.json": graph,
            "region_resolution.json": resolution,
            "validation_pre.json": cae_ir.validation_summary,
        }
        for filename, document in documents.items():
            atomic_write_text(paths.resolved / filename, deterministic_json(document) + "\n")
        self.write_state(
            paths,
            RunBundleState(
                run_id=paths.root.name,
                phase=RunBundlePhase.RESOLVED,
                updated_at=_utc_now(),
            ),
        )

    def write_results(
        self,
        paths: RunBundlePaths,
        *,
        summary: ScalarResultSummary,
        probes: tuple[ProbeResult, ...],
        verification: PhysicalVerificationReport,
        quality: ResultQualitySummary,
    ) -> None:
        """Persist scalar, probe, and post-validation results."""

        atomic_write_text(paths.results / "summary.json", deterministic_json(summary) + "\n")
        atomic_write_text(
            paths.results / "validation_post.json",
            json.dumps(
                {
                    "verification": verification.model_dump(mode="json"),
                    "quality": quality.model_dump(mode="json"),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            (
                "name",
                "requested_x_m",
                "requested_y_m",
                "requested_z_m",
                "coordinate_system",
                "mapped_x_m",
                "mapped_y_m",
                "mapped_z_m",
                "inside_mesh",
                "interpolation_status",
                "value_K",
            )
        )
        for probe in probes:
            mapped = (
                ("", "", "") if probe.mapped_position_m is None else probe.mapped_position_m.root
            )
            writer.writerow(
                (
                    probe.name,
                    *probe.requested_position_m.root,
                    probe.coordinate_system,
                    *mapped,
                    str(probe.inside_mesh).lower(),
                    probe.interpolation_status.value,
                    "" if probe.value_K is None else f"{probe.value_K:.17g}",
                )
            )
        atomic_write_text(paths.results / "probes.csv", output.getvalue())
        self.write_state(
            paths,
            RunBundleState(
                run_id=paths.root.name,
                phase=RunBundlePhase.POSTPROCESSED,
                execution_status=quality.execution.status,
                updated_at=_utc_now(),
            ),
        )

    def finalize(
        self,
        paths: RunBundlePaths,
        *,
        started_at: str,
        execution_status: ExecutionStatus,
        validation_status: VerificationStatus,
        ansys_release: str | None,
        packages: dict[str, str],
        backend_capabilities: dict[str, Any],
        input_hashes: dict[str, str],
        cae_ir_sha256: str | None,
        geometry_sha256: str | None,
        mesh_sha256: str | None,
    ) -> RunBundleManifest:
        """Hash existing artifacts and atomically write the final provenance manifest."""

        records: list[ArtifactDigest] = []
        roots = (
            (paths.request, ArtifactKind.REQUEST),
            (paths.resolved, ArtifactKind.RESOLVED),
            (paths.results, ArtifactKind.RESULT),
            (paths.artifacts, ArtifactKind.ARTIFACT),
            (paths.logs, ArtifactKind.LOG),
        )
        media_types = {
            ".json": "application/json",
            ".yaml": "application/yaml",
            ".csv": "text/csv",
            ".h5": "application/x-hdf5",
            ".log": "text/plain",
        }
        for directory, kind in roots:
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                records.append(
                    ArtifactDigest(
                        path=path.relative_to(paths.root).as_posix(),
                        kind=kind,
                        media_type=media_types.get(path.suffix.lower(), "application/octet-stream"),
                        size_bytes=path.stat().st_size,
                        sha256=_sha256_path(path),
                    )
                )
        git_commit, git_dirty = _git_identity()
        manifest = RunBundleManifest(
            run_id=paths.root.name,
            started_at=started_at,
            finished_at=_utc_now(),
            host_os=f"{platform.system()} {platform.release()}",
            python_version=platform.python_version(),
            ansys_release=ansys_release,
            pyansys_packages=packages,
            backend_capabilities=backend_capabilities,
            input_hashes=input_hashes,
            cae_ir_sha256=cae_ir_sha256,
            geometry_sha256=geometry_sha256,
            mesh_sha256=mesh_sha256,
            execution_status=execution_status,
            validation_status=validation_status,
            artifacts=tuple(records),
            git_commit=git_commit,
            git_dirty=git_dirty,
        )
        atomic_write_text(paths.root / "manifest.json", deterministic_json(manifest) + "\n")
        self.write_state(
            paths,
            RunBundleState(
                run_id=paths.root.name,
                phase=RunBundlePhase.FINALIZED,
                execution_status=execution_status,
                updated_at=_utc_now(),
            ),
        )
        return manifest


def _git_identity() -> tuple[str | None, bool | None]:
    repository = RunnerPaths.from_environment().root
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit or None, bool(status.strip())


def model_sha256(model: BaseModel) -> str:
    """Return the deterministic SHA-256 of a Pydantic contract."""

    return _sha256_bytes(deterministic_json(model).encode())
