"""G7 adapter contract tests with no Ansys process."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ansys_research_runner.adapters.workbench.pyworkbench import (
    execute_workbench_coupling_probe,
)


class FakeMechanical:
    is_alive = True
    version = "26.1"

    def __init__(self) -> None:
        self.exited = False

    def get_product_info(self) -> str:
        return "Mechanical Enterprise"

    def exit(self, force: bool = False) -> None:
        assert force is False
        self.exited = True


class FakeWorkbench:
    server_version = 261

    def __init__(self, server_dir: Path, client_dir: Path, *, fail_create: bool = False) -> None:
        self.server_dir = server_dir
        self.client_dir = client_dir
        self.fail_create = fail_create
        self.scripts: list[str] = []
        self.stopped_systems: list[str] = []
        self.exited = False

    def run_script_string(self, script_string: str, *, log_level: str = "error") -> Any:
        self.scripts.append(script_string)
        assert log_level == "info"
        if self.fail_create:
            raise RuntimeError("injected reviewed-script failure")
        common = {
            "framework_version": "26.1",
            "project_file": str(self.server_dir / "G7Coupling.wbpj"),
            "project_exists": True,
            "system_count": 1,
            "system_names": ["SYS"],
            "system_display_texts": ["G7 Structural"],
            "messages": [],
        }
        if '"operation": "create_save"' in script_string:
            (self.server_dir / "G7Coupling.wbpj").write_bytes(b"project")
            return {"operation": "create_save", **common}
        return {"operation": "reopen", **common}

    def download_project_archive(
        self,
        archive_name: str,
        *,
        include_solution_result_files: bool = True,
        show_progress: bool = True,
    ) -> None:
        assert archive_name == "G7Archive"
        assert include_solution_result_files is False
        assert show_progress is False
        (self.client_dir / "G7Archive.wbpz").write_bytes(b"archive")

    def start_mechanical_server(self, system_name: str, port: int = 0) -> int:
        assert system_name == "SYS"
        assert port == 0
        return 50052

    def stop_mechanical_server(self, system_name: str) -> None:
        self.stopped_systems.append(system_name)

    def exit(self) -> None:
        self.exited = True


def test_reviewed_adapter_completes_lifecycle_archive_and_handoff(tmp_path: Path) -> None:
    client: FakeWorkbench | None = None
    mechanical = FakeMechanical()

    def factory(**kwargs: object) -> FakeWorkbench:
        nonlocal client
        client = FakeWorkbench(
            Path(str(kwargs["server_workdir"])), Path(str(kwargs["client_workdir"]))
        )
        return client

    def connector(**kwargs: object) -> FakeMechanical:
        assert kwargs == {
            "ip": "127.0.0.1",
            "port": 50052,
            "connect_timeout": 60,
            "cleanup_on_exit": False,
        }
        return mechanical

    evidence = execute_workbench_coupling_probe(
        tmp_path,
        workbench_factory=factory,
        mechanical_connector=connector,
    )

    assert evidence["lifecycle_complete"] is True
    assert evidence["mechanical_server_started"] is True
    assert evidence["pymechanical_handoff"] is True
    assert evidence["system_configuration_equal"] is True
    assert evidence["project"]["sha256"]
    assert evidence["archive"]["sha256"]
    assert client is not None
    assert len(client.scripts) == 2
    assert client.stopped_systems == ["SYS"]
    assert client.exited
    assert not mechanical.exited


def test_handoff_failure_does_not_invalidate_project_lifecycle(tmp_path: Path) -> None:
    client: FakeWorkbench | None = None

    def factory(**kwargs: object) -> FakeWorkbench:
        nonlocal client
        client = FakeWorkbench(
            Path(str(kwargs["server_workdir"])), Path(str(kwargs["client_workdir"]))
        )
        return client

    def connector(**kwargs: object) -> FakeMechanical:
        del kwargs
        raise OSError("injected PyMechanical handoff failure")

    evidence = execute_workbench_coupling_probe(
        tmp_path,
        workbench_factory=factory,
        mechanical_connector=connector,
    )

    assert evidence["lifecycle_complete"] is True
    assert evidence["mechanical_server_started"] is True
    assert evidence["pymechanical_handoff"] is False
    assert evidence["handoff_error"] == {
        "type": "OSError",
        "message": "injected PyMechanical handoff failure",
    }
    assert client is not None and client.exited


def test_lifecycle_exception_still_requests_normal_client_exit(tmp_path: Path) -> None:
    client: FakeWorkbench | None = None

    def factory(**kwargs: object) -> FakeWorkbench:
        nonlocal client
        client = FakeWorkbench(
            Path(str(kwargs["server_workdir"])),
            Path(str(kwargs["client_workdir"])),
            fail_create=True,
        )
        return client

    with pytest.raises(RuntimeError, match="injected reviewed-script failure"):
        execute_workbench_coupling_probe(
            tmp_path,
            workbench_factory=factory,
            mechanical_connector=lambda **_: FakeMechanical(),
        )

    assert client is not None and client.exited
