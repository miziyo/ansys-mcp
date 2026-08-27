"""Reusable G8 CLI/application workspace fixtures."""

from __future__ import annotations

from pathlib import Path

import yaml

from ansys_research_runner.adapters.geometry.base import TestGeometryKind, TestGeometrySpec
from ansys_research_runner.adapters.geometry.synthetic import SyntheticGeometryAdapter
from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.services.application_service import ResearchRunnerApplication
from ansys_research_runner.services.job_registry import JobRegistry
from tests.g2_fixtures import box_manifest, steady_recipe


def write_yaml(path: Path, payload: object) -> None:
    """Write deterministic test YAML."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def build_g8_workspace(tmp_path: Path) -> tuple[ResearchRunnerApplication, Path, Path]:
    """Create a complete solver-free CLI workspace and application service."""

    project_root = tmp_path / "project"
    input_root = project_root / "inputs"
    runtime = project_root / "runtime"
    for directory in (input_root, runtime):
        directory.mkdir(parents=True, exist_ok=True)
    adapter = SyntheticGeometryAdapter()
    model_path = adapter.generate_test_asset(
        TestGeometrySpec(kind=TestGeometryKind.BOX),
        input_root,
    )
    manifest = box_manifest().model_copy(
        update={"model": box_manifest().model.model_copy(update={"file": model_path.name})}
    )
    manifest_path = input_root / "box.manifest.yaml"
    write_yaml(manifest_path, manifest.model_dump(mode="json", by_alias=True))
    recipe = steady_recipe().model_copy(
        update={
            "run": steady_recipe().run.model_copy(update={"model_manifest": manifest_path.name})
        }
    )
    recipe_path = input_root / "steady.recipe.yaml"
    write_yaml(recipe_path, recipe.model_dump(mode="json", by_alias=True))
    paths = RunnerPaths(
        root=project_root,
        runtime=runtime,
        runs=runtime / "runs",
        gates=runtime / "gates",
        blockers=runtime / "blockers",
        database=runtime / "jobs.sqlite",
    )

    service = ResearchRunnerApplication(
        paths=paths,
        registry=JobRegistry(paths.database),
        geometry_adapter_factory=SyntheticGeometryAdapter,
        allowed_input_root=input_root,
        allow_test_backends=True,
    )
    return service, recipe_path, model_path
