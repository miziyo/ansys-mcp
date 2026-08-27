"""G7 capability classification tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import psutil

from ansys_research_runner.domain.capabilities import CapabilityStatus
from ansys_research_runner.services.workbench_capability_service import (
    _cleanup_detached_workbench,
    collect_workbench_coupling_capability,
)


def _details(*, handoff: bool) -> dict[str, object]:
    return {
        "framework_version": "26.1",
        "server_version": 261,
        "reviewed_scripts": ["G7_CREATE_SAVE_V1", "G7_REOPEN_V1"],
        "create_save": {"system_names": ["SYS"]},
        "reopen": {"system_names": ["SYS"]},
        "project": {
            "path": "runtime/project.wbpj",
            "exists": True,
            "size_bytes": 10,
            "sha256": "1" * 64,
        },
        "archive": {
            "path": "runtime/project.wbpz",
            "exists": True,
            "size_bytes": 12,
            "sha256": "2" * 64,
        },
        "system_configuration_equal": True,
        "lifecycle_complete": True,
        "mechanical_server_started": True,
        "pymechanical_handoff": handoff,
        "handoff_error": None
        if handoff
        else {"type": "OSError", "message": "gRPC handoff blocked"},
        "normal_exit_requested": True,
        "local_launcher_cleanup": {"remaining": []},
        "owned_process_cleanup": {"remaining": []},
    }


def test_project_lifecycle_passes_while_handoff_capability_remains_false(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ansys_research_runner.services.workbench_capability_service.run_child_probe",
        lambda *_, **__: (CapabilityStatus.AVAILABLE, _details(handoff=False), None),
    )

    report = collect_workbench_coupling_capability()

    assert report.status == "PASSED"
    assert report.lifecycle_complete
    assert report.project_created_saved_reopened
    assert report.mechanical_server_started
    assert not report.pymechanical_handoff
    assert not report.workbench_coupling
    assert report.blocker_stage == "pymechanical_handoff"
    assert report.blocker_reason == "gRPC handoff blocked"


def test_complete_handoff_activates_workbench_coupling(monkeypatch) -> None:
    monkeypatch.setattr(
        "ansys_research_runner.services.workbench_capability_service.run_child_probe",
        lambda *_, **__: (CapabilityStatus.AVAILABLE, _details(handoff=True), None),
    )

    report = collect_workbench_coupling_capability()

    assert report.status == "PASSED"
    assert report.workbench_coupling
    assert report.blocker_stage is None


def test_missing_cleanup_evidence_fails_the_gate(monkeypatch) -> None:
    details = _details(handoff=True)
    details["owned_process_cleanup"] = {"remaining": [1234]}
    monkeypatch.setattr(
        "ansys_research_runner.services.workbench_capability_service.run_child_probe",
        lambda *_, **__: (CapabilityStatus.AVAILABLE, details, None),
    )

    report = collect_workbench_coupling_capability()

    assert report.status == "FAILED"
    assert not report.workbench_coupling
    assert report.blocker_stage == "owned_process_cleanup"


def test_detached_workbench_cleanup_requires_persisted_identity_and_workdir(
    tmp_path: Path,
) -> None:
    probe_dir = tmp_path / "probe"
    server_dir = probe_dir / "server"
    server_dir.mkdir(parents=True)
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import time; time.sleep(30)", str(server_dir)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    identity = psutil.Process(process.pid)
    (probe_dir / "ownership.json").write_text(
        json.dumps(
            {
                "pid": process.pid,
                "create_time": identity.create_time(),
                "server_workdir": str(server_dir),
                "command_line_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    report = _cleanup_detached_workbench(probe_dir)

    process.wait(timeout=2)
    assert report["ownership_recorded"] is True
    assert report["remaining"] == []
