"""Application boundary used by the CLI and, later, the MCP facade."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ansys_research_runner.adapters.geometry.base import (
    GeometryAdapter,
    GeometryInspectionRequest,
)
from ansys_research_runner.adapters.geometry.pyansys_geometry import PyAnsysGeometryAdapter
from ansys_research_runner.config import RunnerPaths, resource_path
from ansys_research_runner.domain.application import (
    ArtifactsCommandResult,
    DoctorCommandResult,
    InspectCommandResult,
    PlanCommandResult,
    RecoveryCommandResult,
    ResolveCommandResult,
    ResultsCommandResult,
    RunCommandResult,
    StatusCommandResult,
    ValidateCommandResult,
)
from ansys_research_runner.domain.blueprint import AnalysisBlueprint
from ansys_research_runner.domain.cae_ir import BackendTarget, ResolvedCAEIR
from ansys_research_runner.domain.errors import DomainError, ErrorCode
from ansys_research_runner.domain.geometry import GeometryGraph
from ansys_research_runner.domain.jobs import JobRecord
from ansys_research_runner.domain.recipe import (
    MeshPolicyDocument,
    ModelManifest,
    RunRecipe,
    TimeSeriesVolumetricHeatLoad,
)
from ansys_research_runner.domain.selectors import RegionResolution, resolve_regions
from ansys_research_runner.domain.transient import ResolvedHeatGenerationProfile
from ansys_research_runner.domain.validation import ValidationReport, validate_run_contracts
from ansys_research_runner.services.capability_service import (
    collect_capabilities,
    persist_capabilities,
)
from ansys_research_runner.services.compilation_service import compile_cae_ir
from ansys_research_runner.services.contract_service import load_public_yaml_contract
from ansys_research_runner.services.geometry_capability_service import (
    collect_geometry_capabilities,
    persist_geometry_capabilities,
)
from ansys_research_runner.services.input_security_service import InputPathPolicy
from ansys_research_runner.services.job_registry import (
    InvalidJobTransition,
    JobNotFoundError,
    JobRegistry,
)
from ansys_research_runner.services.solver_capability_service import (
    collect_steady_solver_capabilities,
    persist_steady_solver_capabilities,
)
from ansys_research_runner.services.transient_profile_service import (
    load_heat_generation_profile,
)

_YAML_EXTENSIONS = (".yaml", ".yml")
_CAD_EXTENSIONS = (
    ".step",
    ".stp",
    ".x_t",
    ".x_b",
    ".parasolid",
    ".scdoc",
    ".scdocx",
    ".dsco",
    ".pmdb",
)
_TEST_MODEL_EXTENSIONS = (".synthetic.json",)
_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True, slots=True)
class _RunInputs:
    recipe_path: Path
    manifest_path: Path
    model_path: Path
    blueprint: AnalysisBlueprint
    manifest: ModelManifest
    recipe: RunRecipe
    graph: GeometryGraph
    backend: str
    resolution: RegionResolution
    validation: ValidationReport
    mesh_policy: MeshPolicyDocument
    resolved_profiles: dict[str, ResolvedHeatGenerationProfile]


class ResearchRunnerApplication:
    """Coordinate contracts, adapters, compilation, and durable jobs."""

    def __init__(
        self,
        *,
        paths: RunnerPaths | None = None,
        registry: JobRegistry | None = None,
        geometry_adapter_factory: Callable[[], GeometryAdapter] | None = None,
        allowed_input_root: Path | None = None,
        maximum_yaml_bytes: int = 1_048_576,
        allow_test_backends: bool = False,
    ) -> None:
        self.paths = paths or RunnerPaths.from_environment()
        self.paths.ensure_runtime()
        self.registry = registry or JobRegistry(self.paths.database)
        self._input_policy = InputPathPolicy(allowed_input_root or self.paths.root)
        self._maximum_yaml_bytes = maximum_yaml_bytes
        self._allow_test_backends = allow_test_backends
        self._geometry_adapter_factory = geometry_adapter_factory or PyAnsysGeometryAdapter

    def doctor(self, *, live: bool, timeout_seconds: float) -> DoctorCommandResult:
        """Collect and persist the main host/product capability report."""

        report = collect_capabilities(live=live, probe_timeout_seconds=timeout_seconds)
        persist_capabilities(report)
        payload = report.model_dump(mode="json")
        if live and not report.mechanical_ready:
            raise DomainError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "doctor.mechanical",
                "Required live Mechanical capability is unavailable.",
                details={"report": payload},
            )
        return DoctorCommandResult(report=payload)

    def geometry_doctor(self, *, live: bool, timeout_seconds: float) -> DoctorCommandResult:
        """Collect and persist the Geometry capability report."""

        report = collect_geometry_capabilities(
            live=live,
            probe_timeout_seconds=timeout_seconds,
        )
        persist_geometry_capabilities(report)
        payload = report.model_dump(mode="json")
        if live and not report.geometry_ready:
            raise DomainError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "geometry-doctor",
                "Required live Geometry capability is unavailable.",
                details={"report": payload},
            )
        return DoctorCommandResult(report=payload)

    def solver_doctor(self, *, live: bool, timeout_seconds: float) -> DoctorCommandResult:
        """Collect and persist the steady-solver capability report."""

        if not live:
            raise DomainError(
                ErrorCode.CONTRACT_INVALID,
                "solver-doctor.live",
                "solver-doctor requires --live evidence.",
            )
        report = collect_steady_solver_capabilities(probe_timeout_seconds=timeout_seconds)
        persist_steady_solver_capabilities(report)
        payload = report.model_dump(mode="json")
        if not report.steady_solver_ready:
            raise DomainError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                "solver-doctor",
                "Required steady-solver capability is unavailable.",
                details={"report": payload},
            )
        return DoctorCommandResult(report=payload)

    def inspect(self, model: str | Path) -> InspectCommandResult:
        """Inspect one confined supported model through the configured adapter."""

        model_path = self._resolve_model(model, base=self._input_policy.root, label="model")
        graph, backend = self._inspect_model(model_path)
        return InspectCommandResult(
            model_path=self._input_policy.relative_text(model_path),
            backend=backend,
            geometry=graph,
        )

    def resolve(self, recipe: str | Path) -> ResolveCommandResult:
        """Resolve every semantic role referenced by one recipe."""

        loaded = self._load_run_inputs(recipe, require_plan=False)
        if not loaded.resolution.successful:
            first = next(
                item.error for item in loaded.resolution.roles.values() if item.error is not None
            )
            raise DomainError(
                first.code,
                first.path,
                first.message,
                details={
                    **first.details,
                    "resolution": loaded.resolution.model_dump(mode="json"),
                },
            )
        return ResolveCommandResult(
            recipe_path=self._input_policy.relative_text(loaded.recipe_path),
            manifest_path=self._input_policy.relative_text(loaded.manifest_path),
            model_path=self._input_policy.relative_text(loaded.model_path),
            resolution=loaded.resolution,
        )

    def validate(self, recipe: str | Path) -> ValidateCommandResult:
        """Run complete cross-contract preflight validation."""

        loaded = self._load_run_inputs(recipe, require_plan=False)
        if not loaded.validation.valid:
            raise DomainError(
                ErrorCode.PREFLIGHT_VALIDATION_FAILED,
                "run",
                "Run contracts failed preflight validation.",
                details={
                    "issues": [item.model_dump(mode="json") for item in loaded.validation.issues]
                },
            )
        return ValidateCommandResult(
            recipe_path=self._input_policy.relative_text(loaded.recipe_path),
            validation=loaded.validation,
        )

    def plan(self, recipe: str | Path, *, run_id: str | None = None) -> PlanCommandResult:
        """Compile a validated recipe into immutable solver-bound CAE-IR."""

        loaded = self._load_run_inputs(recipe, require_plan=True)
        active_run_id = run_id or f"plan-{loaded.recipe.run.case_id}"
        self._validate_run_id(active_run_id)
        cae_ir = self._compile(loaded, active_run_id)
        return PlanCommandResult(
            recipe_path=self._input_policy.relative_text(loaded.recipe_path),
            run_id=active_run_id,
            cae_ir=cae_ir,
        )

    def run(self, recipe: str | Path, *, run_id: str | None = None) -> RunCommandResult:
        """Validate, compile, and durably enqueue a non-blocking thermal job."""

        loaded = self._load_run_inputs(recipe, require_plan=True)
        active_run_id = run_id or f"{loaded.recipe.run.case_id}-{uuid.uuid4().hex[:12]}"
        self._validate_run_id(active_run_id)
        cae_ir = self._compile(loaded, active_run_id)
        request = {
            "schema_version": 1,
            "recipe_path": self._input_policy.relative_text(loaded.recipe_path),
            "manifest_path": self._input_policy.relative_text(loaded.manifest_path),
            "model_path": self._input_policy.relative_text(loaded.model_path),
            "geometry_backend": loaded.backend,
            "cae_ir": cae_ir.model_dump(mode="json", by_alias=True),
        }
        try:
            job = self.registry.create_job(
                active_run_id,
                kind=loaded.recipe.run.blueprint.compact(),
                request=request,
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError(
                ErrorCode.JOB_CONFLICT,
                "run_id",
                f"Job {active_run_id!r} already exists.",
            ) from exc
        return RunCommandResult(job=job)

    def status(self, run_id: str) -> StatusCommandResult:
        """Return a durable snapshot and audit events for one job."""

        job = self._get_job(run_id)
        return StatusCommandResult(job=job, events=self.registry.list_events(run_id))

    def cancel(self, run_id: str) -> StatusCommandResult:
        """Request cancellation using the registry state machine."""

        self._get_job(run_id)
        try:
            job = self.registry.request_cancel(run_id, message="CLI cancellation request")
        except InvalidJobTransition as exc:
            raise DomainError(
                ErrorCode.JOB_STATE_INVALID,
                "run_id",
                str(exc),
                details={"run_id": run_id},
            ) from exc
        return StatusCommandResult(job=job, events=self.registry.list_events(run_id))

    def results(self, run_id: str) -> ResultsCommandResult:
        """Return small structured results without embedding field arrays."""

        job = self._get_job(run_id)
        summary_path = (self.paths.runs / run_id / "results" / "summary.json").resolve()
        root = self.paths.runs.resolve()
        summary: dict[str, Any] | None = None
        if summary_path.is_relative_to(root) and summary_path.is_file():
            if summary_path.stat().st_size > 1_048_576:
                raise DomainError(
                    ErrorCode.CONTRACT_INVALID,
                    "results.summary",
                    "Result summary exceeds the one-megabyte response boundary.",
                )
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise DomainError(
                    ErrorCode.CONTRACT_INVALID,
                    "results.summary",
                    "Result summary is not valid UTF-8 JSON.",
                ) from exc
            if not isinstance(payload, dict):
                raise DomainError(
                    ErrorCode.CONTRACT_INVALID,
                    "results.summary",
                    "Result summary must be a JSON object.",
                )
            summary = payload
        return ResultsCommandResult(
            job_id=run_id,
            status=job.status,
            worker_result=job.result,
            summary=summary,
        )

    def artifacts(self, run_id: str) -> ArtifactsCommandResult:
        """List integrity metadata without returning artifact contents."""

        self._get_job(run_id)
        return ArtifactsCommandResult(
            job_id=run_id,
            artifacts=self.registry.list_artifacts(run_id),
        )

    def recover(self, *, heartbeat_grace_s: float = 0.0) -> RecoveryCommandResult:
        """Recover expired leases and requeue only jobs that never crossed solve."""

        recovered = self.registry.recover_stale(heartbeat_grace_s=heartbeat_grace_s)
        requeued: list[str] = []
        manual_required: list[str] = []
        for run_id in recovered:
            try:
                self.registry.requeue_recoverable(run_id)
            except InvalidJobTransition:
                manual_required.append(run_id)
            else:
                requeued.append(run_id)
        return RecoveryCommandResult(
            recovered=recovered,
            requeued=tuple(requeued),
            manual_required=tuple(manual_required),
        )

    def _load_run_inputs(self, recipe: str | Path, *, require_plan: bool) -> _RunInputs:
        recipe_path = self._input_policy.resolve_file(
            recipe,
            allowed_extensions=_YAML_EXTENSIONS,
            path_label="recipe",
        )
        recipe_document = self._load_contract(recipe_path, RunRecipe)
        manifest_path = self._input_policy.resolve_file(
            recipe_document.run.model_manifest,
            base=recipe_path.parent,
            allowed_extensions=_YAML_EXTENSIONS,
            path_label="run.model_manifest",
        )
        manifest = self._load_contract(manifest_path, ModelManifest)
        model_path = self._resolve_model(
            manifest.model.file,
            base=manifest_path.parent,
            label="model.file",
        )
        graph, backend = self._inspect_model(model_path)
        resolution = resolve_regions(graph, manifest.roles, manifest.coordinate_frame)
        blueprint_path = resource_path(
            "blueprints",
            f"{recipe_document.run.blueprint.id}.v{recipe_document.run.blueprint.version}.yaml",
        )
        blueprint = self._load_internal_contract(blueprint_path, AnalysisBlueprint)
        validation = validate_run_contracts(
            blueprint,
            manifest.roles,
            recipe_document,
            graph,
            resolution,
        )
        mesh_policy = self._load_internal_contract(
            resource_path("policies", "mesh.v1.yaml"),
            MeshPolicyDocument,
        )
        profiles = (
            self._resolve_profiles(recipe_document, recipe_path.parent) if require_plan else {}
        )
        return _RunInputs(
            recipe_path=recipe_path,
            manifest_path=manifest_path,
            model_path=model_path,
            blueprint=blueprint,
            manifest=manifest,
            recipe=recipe_document,
            graph=graph,
            backend=backend,
            resolution=resolution,
            validation=validation,
            mesh_policy=mesh_policy,
            resolved_profiles=profiles,
        )

    def _load_contract[ContractT: Any](self, path: Path, model_type: type[ContractT]) -> ContractT:
        return load_public_yaml_contract(
            path,
            model_type,
            maximum_bytes=self._maximum_yaml_bytes,
        )

    def _load_internal_contract[ContractT: Any](
        self, path: Path, model_type: type[ContractT]
    ) -> ContractT:
        resolved = path.resolve()
        resources = resource_path().resolve()
        if not resolved.is_relative_to(resources) or not resolved.is_file():
            raise DomainError(
                ErrorCode.FILE_NOT_FOUND,
                "internal_contract",
                "Required versioned project contract is missing.",
                details={"path": str(path)},
            )
        return self._load_contract(resolved, model_type)

    def _resolve_model(self, value: str | Path, *, base: Path, label: str) -> Path:
        extensions = _CAD_EXTENSIONS + (_TEST_MODEL_EXTENSIONS if self._allow_test_backends else ())
        return self._input_policy.resolve_file(
            value,
            base=base,
            allowed_extensions=extensions,
            path_label=label,
        )

    def _inspect_model(self, model_path: Path) -> tuple[GeometryGraph, str]:
        adapter = self._geometry_adapter_factory()
        try:
            capability = adapter.probe_capabilities()
            if not capability.available:
                raise DomainError(
                    ErrorCode.GEOMETRY_CAPABILITY_MISSING,
                    "geometry_adapter",
                    "Configured Geometry adapter is unavailable.",
                    details={
                        "backend": capability.backend,
                        "reason": capability.reason,
                        "missing_capabilities": list(capability.missing_capabilities),
                    },
                )
            graph = adapter.inspect(GeometryInspectionRequest(model_path=model_path))
            return graph, capability.backend
        finally:
            adapter.close()

    def _resolve_profiles(
        self, recipe: RunRecipe, base: Path
    ) -> dict[str, ResolvedHeatGenerationProfile]:
        loads = [
            item
            for item in recipe.boundary_conditions
            if isinstance(item, TimeSeriesVolumetricHeatLoad)
        ]
        if not loads:
            return {}
        if recipe.analysis.end_time is None:
            raise DomainError(
                ErrorCode.CONTRACT_INVALID,
                "analysis.end_time",
                "Transient time profiles require analysis.end_time.",
            )
        profiles: dict[str, ResolvedHeatGenerationProfile] = {}
        for index, load in enumerate(loads):
            if load.region in profiles:
                raise DomainError(
                    ErrorCode.CONTRACT_INVALID,
                    f"boundary_conditions[{index}].region",
                    "Multiple time profiles target the same semantic region.",
                )
            path = self._input_policy.resolve_file(
                load.profile_file,
                base=base,
                allowed_extensions=(".csv",),
                path_label=f"boundary_conditions[{index}].profile_file",
            )
            try:
                profiles[load.region] = load_heat_generation_profile(
                    path,
                    expected_end_time_s=recipe.analysis.end_time.si_value,
                )
            except ValueError as exc:
                raise DomainError(
                    ErrorCode.CONTRACT_INVALID,
                    f"boundary_conditions[{index}].profile_file",
                    str(exc),
                ) from exc
        return profiles

    @staticmethod
    def _compile(inputs: _RunInputs, run_id: str) -> ResolvedCAEIR:
        return compile_cae_ir(
            run_id=run_id,
            blueprint=inputs.blueprint,
            manifest=inputs.manifest,
            recipe=inputs.recipe,
            graph=inputs.graph,
            resolution=inputs.resolution,
            mesh_policy=inputs.mesh_policy,
            backend_target=BackendTarget.MAPDL,
            resolved_time_profiles=inputs.resolved_profiles,
        )

    def _get_job(self, run_id: str) -> JobRecord:
        try:
            return self.registry.get_job(run_id)
        except JobNotFoundError as exc:
            raise DomainError(
                ErrorCode.JOB_NOT_FOUND,
                "run_id",
                f"Job {run_id!r} does not exist.",
            ) from exc

    @staticmethod
    def _validate_run_id(run_id: str) -> None:
        if not _JOB_ID_PATTERN.fullmatch(run_id):
            raise DomainError(
                ErrorCode.CONTRACT_INVALID,
                "run_id",
                "Run ID must use 1-128 ASCII letters, digits, dot, underscore, or hyphen.",
            )
