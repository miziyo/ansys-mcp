from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ansys_research_runner import __version__
from ansys_research_runner.config import resource_path
from scripts.audit_public_repository import _is_denied_path, _tracked_paths, audit_tree


def test_public_audit_rejects_generated_and_private_paths() -> None:
    assert _is_denied_path("runtime/jobs.sqlite")
    assert _is_denied_path("artifacts/result.rst")
    assert _is_denied_path("tests/legacy/test_old.py")
    assert _is_denied_path("notes-codex.md")
    assert _is_denied_path("tutorials/qualification/example.json")
    assert _is_denied_path("scripts/run_product_tutorial.py")
    assert _is_denied_path("models/copied-example.step")
    assert _is_denied_path("models/copied-example.stp")
    assert not _is_denied_path("src/ansys_research_runner/resources/geometry/g3_box.step")


def test_public_tree_contains_no_tutorial_or_solver_output_material() -> None:
    tracked = _tracked_paths()
    prohibited_suffixes = {
        ".aedt",
        ".agdb",
        ".cdb",
        ".cas",
        ".dat",
        ".dsco",
        ".glb",
        ".rst",
        ".rth",
        ".twin",
        ".wbpz",
    }

    assert not any("tutorial" in path.casefold() for path in tracked)
    assert not any(path.casefold().endswith(tuple(prohibited_suffixes)) for path in tracked)


def test_python_and_pi_package_versions_match() -> None:
    root = Path(__file__).resolve().parents[2]
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))

    assert package["version"] == __version__
    assert lock["version"] == __version__
    extension = (root / "integrations" / "pi" / "index.ts").read_text(encoding="utf-8")
    assert f'version: "{__version__}"' in extension


def test_readme_language_navigation_is_complete() -> None:
    root = Path(__file__).resolve().parents[2]
    readmes = ("README.md", "README.ko.md", "README.ja.md")

    for source_name in readmes:
        source = (root / source_name).read_text(encoding="utf-8")
        for target_name in readmes:
            assert f"]({target_name})" in source


def test_project_owned_geometry_assets_match_their_generators_manifest() -> None:
    assets = resource_path("geometry")
    manifest = json.loads((assets / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["license"] == "MIT"
    for record in manifest["assets"]:
        asset_sha256 = hashlib.sha256((assets / record["file"]).read_bytes()).hexdigest()
        assert asset_sha256 == record["sha256"]
        assert (
            hashlib.sha256((assets / record["generator"]).read_bytes()).hexdigest()
            == record["generator_sha256"]
        )


def test_current_public_tree_has_no_findings() -> None:
    findings, scanned = audit_tree()

    assert findings == []
    assert scanned > 150
