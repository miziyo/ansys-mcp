"""Reviewed fixed-script PyWorkbench lifecycle and Mechanical handoff probe."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self, cast

import psutil

from ansys_research_runner.adapters.workbench.base import (
    MechanicalClient,
    MechanicalConnector,
    WorkbenchClient,
    WorkbenchFactory,
)
from ansys_research_runner.io import atomic_write_json

_CREATE_PROJECT = r"""
import json
import os
Reset()
work_dir = GetServerWorkingDirectory()
project_file = os.path.join(work_dir, "G7Coupling.wbpj")
template = GetTemplate(TemplateName="Static Structural", Solver="ANSYS")
system = CreateSystemFromTemplate(Template=template, Name="G7 Structural")
Save(FilePath=project_file, Overwrite=True)
systems = GetAllSystems()
wb_script_result = json.dumps({
    "operation": "create_save",
    "framework_version": str(GetFrameworkVersion()),
    "project_file": str(GetProjectFile()),
    "project_exists": os.path.isfile(project_file),
    "system_count": len(systems),
    "system_names": [str(item.Name) for item in systems],
    "system_display_texts": [str(item.DisplayText) for item in systems],
    "messages": [str(message.Summary) for message in GetMessages()],
})
"""

_REOPEN_PROJECT = r"""
import json
import os
work_dir = GetServerWorkingDirectory()
project_file = os.path.join(work_dir, "G7Coupling.wbpj")
Reset()
Open(FilePath=project_file)
systems = GetAllSystems()
wb_script_result = json.dumps({
    "operation": "reopen",
    "framework_version": str(GetFrameworkVersion()),
    "project_file": str(GetProjectFile()),
    "project_exists": os.path.isfile(project_file),
    "system_count": len(systems),
    "system_names": [str(item.Name) for item in systems],
    "system_display_texts": [str(item.DisplayText) for item in systems],
    "messages": [str(message.Summary) for message in GetMessages()],
})
"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path) -> dict[str, object]:
    exists = path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "sha256": _sha256(path) if exists else None,
    }


def _launcher_pid(client: WorkbenchClient) -> int | None:
    launcher = getattr(client, "_launcher", None)
    value = getattr(launcher, "_process_id", None)
    return int(value) if value is not None else None


def _wait_for_pid(pid: int, timeout_s: float) -> bool:
    try:
        process = psutil.Process(pid)
    except psutil.Error:
        return True
    _, alive = psutil.wait_procs([process], timeout=timeout_s)
    return not alive


def _exact_launcher_cleanup(pid: int) -> dict[str, object]:
    """Clean only a launcher known to have been created by this adapter call."""

    try:
        parent = psutil.Process(pid)
        create_time = parent.create_time()
    except psutil.Error:
        return {"launcher_pid": pid, "already_exited": True, "remaining": []}
    children = parent.children(recursive=True)
    observed = [process.pid for process in children]
    for process in reversed(children):
        with suppress(psutil.Error):
            process.terminate()
    with suppress(psutil.Error):
        parent.terminate()
    _, alive = psutil.wait_procs([*children, parent], timeout=5)
    killed: list[int] = []
    for process in alive:
        with suppress(psutil.Error):
            process.kill()
            killed.append(process.pid)
    _, remaining = psutil.wait_procs(alive, timeout=2)
    return {
        "launcher_pid": pid,
        "launcher_create_time": create_time,
        "observed_descendants": sorted(observed),
        "killed": sorted(killed),
        "remaining": sorted(process.pid for process in remaining),
    }


@dataclass
class WorkbenchMechanicalSession:
    """One run-owned Workbench process and optional Workbench-managed Mechanical server."""

    workdir: Path
    workbench_factory: WorkbenchFactory | None = None
    mechanical_connector: MechanicalConnector | None = None
    client: WorkbenchClient | None = field(init=False, default=None)
    mechanical: MechanicalClient | None = field(init=False, default=None)
    launcher_pid: int | None = field(init=False, default=None)
    server_version: int | None = field(init=False, default=None)
    mechanical_system_name: str | None = field(init=False, default=None)
    mechanical_port: int | None = field(init=False, default=None)
    mechanical_alive: bool | None = field(init=False, default=None)
    mechanical_server_started: bool = field(init=False, default=False)
    normal_exit_requested: bool = field(init=False, default=False)
    closed: bool = field(init=False, default=False)
    local_cleanup: dict[str, object] = field(
        init=False,
        default_factory=lambda: {"remaining": []},
    )

    @property
    def server_dir(self) -> Path:
        """Return the isolated Workbench server directory."""

        return self.workdir.resolve() / "server"

    @property
    def client_dir(self) -> Path:
        """Return the isolated PyWorkbench client directory."""

        return self.workdir.resolve() / "client"

    def _state(self, stage: str, **details: object) -> None:
        with suppress(OSError):
            atomic_write_json(
                self.workdir.resolve() / "probe-state.json", {"stage": stage, **details}
            )

    def open(self) -> Self:
        """Launch Workbench and record exact launcher ownership."""

        if self.client is not None and not self.closed:
            raise RuntimeError("Workbench session is already open.")
        self.closed = False
        self.server_dir.mkdir(parents=True, exist_ok=True)
        self.client_dir.mkdir(parents=True, exist_ok=True)
        if self.workbench_factory is None:
            from ansys.workbench.core.public_api import launch_workbench

            self.workbench_factory = cast(WorkbenchFactory, launch_workbench)
        if self.mechanical_connector is None:
            from ansys.mechanical.core import connect_to_mechanical

            self.mechanical_connector = cast(MechanicalConnector, connect_to_mechanical)
        try:
            self.client = self.workbench_factory(
                show_gui=False,
                version="261",
                client_workdir=str(self.client_dir),
                server_workdir=str(self.server_dir),
            )
            self.server_version = int(self.client.server_version)
            self.launcher_pid = _launcher_pid(self.client)
            if self.launcher_pid is not None:
                launcher = psutil.Process(self.launcher_pid)
                atomic_write_json(
                    self.workdir.resolve() / "ownership.json",
                    {
                        "pid": self.launcher_pid,
                        "create_time": launcher.create_time(),
                        "server_workdir": str(self.server_dir),
                        "command_line_sha256": hashlib.sha256(
                            "\0".join(launcher.cmdline()).encode("utf-8")
                        ).hexdigest(),
                    },
                )
            self._state("workbench_launched")
            return self
        except Exception:
            self.close()
            raise

    def start_mechanical(self, system_name: str) -> MechanicalClient:
        """Start and connect to the Mechanical server owned by one Workbench system."""

        if self.client is None or self.mechanical_connector is None:
            raise RuntimeError("Workbench session must be open before Mechanical handoff.")
        self.mechanical_system_name = system_name
        self._state("mechanical_server_starting", system_name=system_name)
        port = int(self.client.start_mechanical_server(system_name))
        self.mechanical_port = port
        self.mechanical_server_started = port > 0
        if not self.mechanical_server_started:
            raise RuntimeError(f"Workbench returned invalid Mechanical server port: {port}")
        self._state("pymechanical_connecting", port=port)
        self.mechanical = self.mechanical_connector(
            ip="127.0.0.1",
            port=port,
            connect_timeout=60,
            cleanup_on_exit=False,
        )
        self.mechanical_alive = bool(self.mechanical.is_alive)
        self._state("pymechanical_connected", port=port, is_alive=self.mechanical_alive)
        return self.mechanical

    def close(self) -> None:
        """Stop only this session's Mechanical server, Workbench, and exact owned launcher tree."""

        if self.closed:
            return
        self.closed = True
        if (
            self.client is not None
            and self.mechanical_server_started
            and self.mechanical_system_name is not None
        ):
            self._state(
                "mechanical_server_stopping",
                port=self.mechanical_port,
                is_alive=self.mechanical_alive,
            )
            with suppress(Exception):
                self.client.stop_mechanical_server(self.mechanical_system_name)
        if self.mechanical is not None:
            self._state(
                "pymechanical_channel_released_on_worker_exit",
                port=self.mechanical_port,
                is_alive=self.mechanical_alive,
            )
        if self.client is not None:
            self._state("workbench_exiting")
            with suppress(Exception):
                self.client.exit()
                self.normal_exit_requested = True
        if self.launcher_pid is not None and not _wait_for_pid(self.launcher_pid, 5):
            self.local_cleanup = _exact_launcher_cleanup(self.launcher_pid)
        self._state("finished")

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()


def execute_workbench_coupling_probe(
    workdir: Path,
    *,
    workbench_factory: WorkbenchFactory | None = None,
    mechanical_connector: MechanicalConnector | None = None,
    attempt_handoff: bool = True,
) -> dict[str, object]:
    """Create/save/reopen/archive a project and attempt the official Mechanical handoff."""

    session = WorkbenchMechanicalSession(
        workdir=workdir.resolve(),
        workbench_factory=workbench_factory,
        mechanical_connector=mechanical_connector,
    )
    pymechanical_handoff = False
    handoff_error: dict[str, str] | None = None
    created: dict[str, Any]
    reopened: dict[str, Any]
    mechanical_details: dict[str, object] = {}
    try:
        session.open()
        client = session.client
        if client is None:
            raise RuntimeError("Workbench launch returned no client.")
        created_raw = client.run_script_string(_CREATE_PROJECT, log_level="info")
        if not isinstance(created_raw, dict):
            raise RuntimeError("Reviewed create/save script returned no structured result.")
        created = created_raw
        session._state("project_saved")
        reopened_raw = client.run_script_string(_REOPEN_PROJECT, log_level="info")
        if not isinstance(reopened_raw, dict):
            raise RuntimeError("Reviewed reopen script returned no structured result.")
        reopened = reopened_raw
        session._state("project_reopened")
        client.download_project_archive(
            "G7Archive",
            include_solution_result_files=False,
            show_progress=False,
        )
        session._state("archive_downloaded")
        names = reopened.get("system_names")
        if not isinstance(names, list) or len(names) != 1:
            raise RuntimeError("Reopened project did not expose exactly one system.")
        mechanical_system_name = str(names[0])
        atomic_write_json(
            workdir / "lifecycle-evidence.json",
            {
                "create_save": created,
                "reopen": reopened,
                "project": _artifact(session.server_dir / "G7Coupling.wbpj"),
                "archive": _artifact(session.client_dir / "G7Archive.wbpz"),
            },
        )
        if attempt_handoff:
            try:
                mechanical = session.start_mechanical(mechanical_system_name)
                pymechanical_handoff = bool(mechanical.is_alive)
                mechanical_details = {
                    "port": session.mechanical_port,
                    "is_alive": pymechanical_handoff,
                    "version": str(mechanical.version),
                    "product_info": str(mechanical.get_product_info()),
                }
            except Exception as exc:  # noqa: BLE001 - optional capability result
                handoff_error = {"type": type(exc).__name__, "message": str(exc)}
                session._state(
                    "pymechanical_handoff_failed",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
    finally:
        session.close()

    project = _artifact(session.server_dir / "G7Coupling.wbpj")
    archive = _artifact(session.client_dir / "G7Archive.wbpz")
    system_equal = bool(
        created.get("system_count") == reopened.get("system_count") == 1
        and created.get("system_names") == reopened.get("system_names")
        and created.get("system_display_texts") == reopened.get("system_display_texts")
    )
    lifecycle_complete = session.server_version == 261 and bool(
        created.get("project_exists")
        and reopened.get("project_exists")
        and system_equal
        and project["exists"]
        and project["size_bytes"]
        and archive["exists"]
        and archive["size_bytes"]
        and session.normal_exit_requested
        and session.local_cleanup.get("remaining") == []
    )
    return {
        "mode": "coupling" if attempt_handoff else "lifecycle",
        "framework_version": str(created.get("framework_version", "")),
        "server_version": session.server_version,
        "reviewed_scripts": ["G7_CREATE_SAVE_V1", "G7_REOPEN_V1"],
        "create_save": created,
        "reopen": reopened,
        "project": project,
        "archive": archive,
        "system_configuration_equal": system_equal,
        "lifecycle_complete": lifecycle_complete,
        "mechanical_server_started": session.mechanical_server_started,
        "pymechanical_handoff": pymechanical_handoff,
        "mechanical": mechanical_details,
        "handoff_error": handoff_error,
        "normal_exit_requested": session.normal_exit_requested,
        "local_launcher_cleanup": session.local_cleanup,
    }
