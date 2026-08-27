"""G7 official PyWorkbench lifecycle and Mechanical handoff capability service."""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from contextlib import suppress
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import psutil

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.capabilities import (
    CapabilityArtifact,
    CapabilityStatus,
    WorkbenchCouplingGateCapabilityReport,
)
from ansys_research_runner.io import atomic_write_json
from ansys_research_runner.services.capability_service import run_child_probe, utc_now


def _version(distribution: str) -> str | None:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return None


def _cleanup_is_complete(value: object) -> bool:
    return isinstance(value, dict) and value.get("remaining") == []


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cleanup_detached_workbench(probe_dir: Path) -> dict[str, object]:
    """Clean a launcher only when its persisted PID/create-time/workdir identity matches."""

    ownership = _read_json(probe_dir / "ownership.json")
    pid = ownership.get("pid")
    create_time = ownership.get("create_time")
    expected_workdir = str((probe_dir / "server").resolve()).lower().replace("\\", "/")
    if not isinstance(pid, int) or not isinstance(create_time, (int, float)):
        return {"ownership_recorded": False, "remaining": []}
    try:
        root = psutil.Process(pid)
        actual_create_time = root.create_time()
        root_command = " ".join(root.cmdline()).lower().replace("\\", "/")
    except psutil.NoSuchProcess:
        return {"ownership_recorded": True, "already_exited": [pid], "remaining": []}
    except (psutil.AccessDenied, psutil.ZombieProcess):
        return {"ownership_recorded": True, "access_denied": [pid], "remaining": [pid]}
    if abs(actual_create_time - float(create_time)) > 0.01 or expected_workdir not in root_command:
        return {"ownership_recorded": True, "identity_mismatch": [pid], "remaining": [pid]}

    try:
        children = root.children(recursive=True)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        children = []
    license_tokens: set[str] = set()
    for process in children:
        with suppress(psutil.Error):
            command = " ".join(process.cmdline())
            match = re.search(r"(?:^|\s)-acl\s+(\d+\.\d+)(?:\s|$)", command)
            if match:
                license_tokens.add(match.group(1))
    requested: list[psutil.Process] = []
    for process in [*reversed(children), root]:
        with suppress(psutil.Error):
            process.terminate()
            requested.append(process)
    gone, alive = psutil.wait_procs(requested, timeout=5)
    killed: list[int] = []
    for process in alive:
        with suppress(psutil.Error):
            process.kill()
            killed.append(process.pid)
    killed_gone, remaining = psutil.wait_procs(alive, timeout=2)
    killed.extend(process.pid for process in killed_gone)

    cleanup_helpers: list[int] = []
    if license_tokens:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            helpers: list[psutil.Process] = []
            for process in psutil.process_iter(["pid", "cmdline"]):
                with suppress(psutil.Error):
                    command = " ".join(process.info.get("cmdline") or [])
                    if any(f"-aclcleanup {token}" in command for token in license_tokens):
                        helpers.append(process)
            if not helpers:
                time.sleep(0.05)
                continue
            for helper in helpers:
                with suppress(psutil.Error):
                    helper.terminate()
                    cleanup_helpers.append(helper.pid)
            _, helper_alive = psutil.wait_procs(helpers, timeout=1)
            for helper in helper_alive:
                with suppress(psutil.Error):
                    helper.kill()
            psutil.wait_procs(helper_alive, timeout=1)
            break
    still_running: list[int] = []
    for process in remaining:
        with suppress(psutil.Error):
            if process.is_running():
                still_running.append(process.pid)
    return {
        "ownership_recorded": True,
        "launcher_pid": pid,
        "observed": sorted({root.pid, *(process.pid for process in children)}),
        "terminated": sorted(process.pid for process in gone),
        "killed": sorted(set(killed)),
        "license_cleanup_helpers": sorted(set(cleanup_helpers)),
        "remaining": sorted(still_running),
    }


def _run_probe_mode(
    probe_dir: Path, mode: str, timeout_seconds: float
) -> tuple[CapabilityStatus, dict[str, object], str | None]:
    status, details, reason = run_child_probe(
        [
            sys.executable,
            "-m",
            "ansys_research_runner.adapters.workbench.g7_probe_worker",
            "--workdir",
            str(probe_dir),
            "--mode",
            mode,
        ],
        timeout_seconds=timeout_seconds,
    )
    details["detached_workbench_cleanup"] = _cleanup_detached_workbench(probe_dir)
    details["last_probe_state"] = _read_json(probe_dir / "probe-state.json")
    details["partial_lifecycle_evidence"] = _read_json(probe_dir / "lifecycle-evidence.json")
    return status, details, reason


def _probe_cleanup_verified(status: CapabilityStatus, details: dict[str, object]) -> bool:
    required = [
        details.get("owned_process_cleanup"),
        details.get("detached_workbench_cleanup"),
    ]
    if status is CapabilityStatus.AVAILABLE:
        required.append(details.get("local_launcher_cleanup"))
    return all(_cleanup_is_complete(item) for item in required)


def collect_workbench_coupling_capability(
    *, probe_timeout_seconds: float = 360
) -> WorkbenchCouplingGateCapabilityReport:
    """Run normal lifecycle and potentially hanging handoff in separate owned probes."""

    paths = RunnerPaths.from_environment()
    root = paths.runtime / "probes" / "g7_workbench" / uuid.uuid4().hex
    per_probe_timeout = max(1.0, probe_timeout_seconds / 2)
    lifecycle_status, lifecycle_details, lifecycle_reason = _run_probe_mode(
        root / "lifecycle", "lifecycle", per_probe_timeout
    )
    coupling_status, coupling_details, coupling_reason = _run_probe_mode(
        root / "coupling", "coupling", per_probe_timeout
    )
    lifecycle_cleanup = _probe_cleanup_verified(lifecycle_status, lifecycle_details)
    coupling_cleanup = _probe_cleanup_verified(coupling_status, coupling_details)
    cleanup_verified = lifecycle_cleanup and coupling_cleanup
    combined_evidence: dict[str, object] = {
        "lifecycle_probe": lifecycle_details,
        "coupling_probe": coupling_details,
        "lifecycle_probe_status": lifecycle_status.value,
        "coupling_probe_status": coupling_status.value,
    }
    if lifecycle_status is not CapabilityStatus.AVAILABLE:
        return WorkbenchCouplingGateCapabilityReport(
            generated_at=utc_now(),
            status="BLOCKED_ENVIRONMENT" if cleanup_verified else "FAILED",
            pyworkbench_version=_version("ansys-workbench-core"),
            pymechanical_version=_version("ansys-mechanical-core"),
            lifecycle_complete=False,
            reviewed_script_execution=False,
            project_created_saved_reopened=False,
            mechanical_server_started=False,
            pymechanical_handoff=False,
            coupling_probe_completed=coupling_status is CapabilityStatus.AVAILABLE,
            workbench_coupling=False,
            normal_exit_requested=False,
            owned_cleanup_verified=cleanup_verified,
            blocker_stage="workbench_lifecycle",
            blocker_reason=lifecycle_reason,
            evidence=combined_evidence,
        )

    project_raw = lifecycle_details.get("project")
    archive_raw = lifecycle_details.get("archive")
    project = (
        CapabilityArtifact.model_validate(project_raw) if isinstance(project_raw, dict) else None
    )
    archive = (
        CapabilityArtifact.model_validate(archive_raw) if isinstance(archive_raw, dict) else None
    )
    reopened = lifecycle_details.get("reopen")
    reopened_dict = reopened if isinstance(reopened, dict) else {}
    raw_names = reopened_dict.get("system_names")
    system_names = tuple(str(value) for value in raw_names) if isinstance(raw_names, list) else ()
    scripts = lifecycle_details.get("reviewed_scripts")
    reviewed = scripts == ["G7_CREATE_SAVE_V1", "G7_REOPEN_V1"]
    project_roundtrip = bool(
        lifecycle_details.get("system_configuration_equal")
        and project is not None
        and project.exists
        and project.size_bytes > 0
        and project.sha256 is not None
        and archive is not None
        and archive.exists
        and archive.size_bytes > 0
        and archive.sha256 is not None
        and len(system_names) == 1
    )
    lifecycle = bool(lifecycle_details.get("lifecycle_complete")) and project_roundtrip and reviewed
    coupling_state = coupling_details.get("last_probe_state")
    server_started = bool(coupling_details.get("mechanical_server_started")) or (
        isinstance(coupling_state, dict)
        and coupling_state.get("stage")
        in {
            "pymechanical_connecting",
            "pymechanical_connected",
            "pymechanical_handoff_failed",
            "pymechanical_exiting",
            "mechanical_server_stopping",
            "pymechanical_channel_released_on_worker_exit",
            "workbench_exiting",
            "finished",
        }
        and isinstance(coupling_state.get("port"), int)
    )
    handoff = bool(coupling_details.get("pymechanical_handoff")) or (
        isinstance(coupling_state, dict) and coupling_state.get("is_alive") is True
    )
    coupling_completed = coupling_status is CapabilityStatus.AVAILABLE
    coupling = lifecycle and coupling_completed and server_started and handoff and cleanup_verified
    blocker_stage = None
    blocker_reason = None
    if not lifecycle:
        blocker_stage = "workbench_lifecycle"
        blocker_reason = "Workbench lifecycle or archive evidence did not satisfy the G7 contract."
    elif not handoff:
        blocker_stage = "pymechanical_handoff"
        handoff_error = coupling_details.get("handoff_error")
        blocker_reason = (
            str(handoff_error.get("message"))
            if isinstance(handoff_error, dict)
            else coupling_reason
            or "Workbench Mechanical service did not yield a live PyMechanical client."
        )
        state = coupling_details.get("last_probe_state")
        if isinstance(state, dict) and state.get("stage"):
            blocker_reason = f"{blocker_reason} Last stage: {state['stage']}."
    elif not coupling_completed:
        blocker_stage = "coupling_shutdown"
        blocker_reason = coupling_reason or "Workbench coupling probe did not exit normally."
        if isinstance(coupling_state, dict) and coupling_state.get("stage"):
            blocker_reason = f"{blocker_reason} Last stage: {coupling_state['stage']}."
    if not cleanup_verified:
        blocker_stage = "owned_process_cleanup"
        blocker_reason = "One or more owned Workbench/Mechanical descendants remained after probe."
    gate_status: Literal["PASSED", "FAILED"] = (
        "PASSED" if lifecycle and cleanup_verified else "FAILED"
    )
    return WorkbenchCouplingGateCapabilityReport(
        generated_at=utc_now(),
        status=gate_status,
        pyworkbench_version=_version("ansys-workbench-core"),
        pymechanical_version=_version("ansys-mechanical-core"),
        framework_version=str(lifecycle_details.get("framework_version") or "") or None,
        lifecycle_complete=lifecycle,
        reviewed_script_execution=reviewed,
        project_created_saved_reopened=project_roundtrip,
        system_names=system_names,
        project=project,
        archive=archive,
        mechanical_server_started=server_started,
        pymechanical_handoff=handoff,
        coupling_probe_completed=coupling_completed,
        workbench_coupling=coupling,
        normal_exit_requested=bool(lifecycle_details.get("normal_exit_requested")),
        owned_cleanup_verified=cleanup_verified,
        blocker_stage=blocker_stage,
        blocker_reason=blocker_reason,
        evidence=combined_evidence,
    )


def persist_workbench_coupling_capability(
    report: WorkbenchCouplingGateCapabilityReport,
) -> None:
    """Persist G7 machine evidence, capability docs, and any handoff blocker."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    atomic_write_json(
        paths.runtime / "workbench_coupling_capability_report.json",
        report.model_dump(mode="json"),
    )
    atomic_write_json(
        paths.blockers / "G7.json",
        {
            "gate": "G7",
            "gate_status": report.status,
            "workbench_coupling": report.workbench_coupling,
            "coupling_probe_completed": report.coupling_probe_completed,
            "blocker_stage": report.blocker_stage,
            "reason": report.blocker_reason,
            "owned_cleanup_verified": report.owned_cleanup_verified,
            "reproduction_command": "ansys-research doctor --live --json",
        },
    )
