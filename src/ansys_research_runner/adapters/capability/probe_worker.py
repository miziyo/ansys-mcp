"""Isolated imports and product launches for capability discovery."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import time
import traceback
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import psutil


def _process_exited(process_id: int, timeout_seconds: float = 5) -> bool:
    try:
        process = psutil.Process(process_id)
    except psutil.Error:
        return True
    _, alive = psutil.wait_procs([process], timeout=timeout_seconds)
    return not alive


def _terminate_owned_tree(process_id: int) -> None:
    """Terminate only the exact launcher process and its observed descendants."""

    try:
        parent = psutil.Process(process_id)
    except psutil.Error:
        return
    children = parent.children(recursive=True)
    for process in reversed(children):
        with suppress(psutil.Error):
            process.terminate()
    with suppress(psutil.Error):
        parent.terminate()
    _, alive = psutil.wait_procs([*children, parent], timeout=5)
    for process in alive:
        with suppress(psutil.Error):
            process.kill()
    time.sleep(0.25)


def _probe_package(module: str) -> dict[str, object]:
    imported = importlib.import_module(module)
    return {"module": module, "module_file": str(getattr(imported, "__file__", ""))}


def _probe_mechanical(executable: Path, workdir: Path) -> dict[str, object]:
    from ansys.mechanical.core import launch_mechanical

    launcher = cast(Callable[..., Any], launch_mechanical)
    mechanical: Any = None
    try:
        mechanical = launcher(
            batch=True,
            cleanup_on_exit=True,
            exec_file=str(executable),
            start_instance=True,
            start_timeout=120,
            transport_mode="insecure",
            version=261,
            additional_envs={"TEMP": str(workdir), "TMP": str(workdir)},
        )
        return {
            "is_alive": bool(mechanical.is_alive),
            "version": str(mechanical.version),
            "product_info": str(mechanical.get_product_info()),
        }
    finally:
        if mechanical is not None:
            mechanical.exit(force=True)


def _probe_mechanical_embedding(workdir: Path) -> dict[str, object]:
    """Probe the official in-process Mechanical embedding mode in an isolated worker."""

    os.environ["TEMP"] = str(workdir)
    os.environ["TMP"] = str(workdir)
    from ansys.mechanical.core import App

    with App(version=261, private_appdata=True) as app:
        return {
            "product_info": str(app.product_info),
            "project_name": str(app.DataModel.Project.Name),
            "mode": "embedding",
        }


def _probe_workbench(executable: Path, workdir: Path) -> dict[str, object]:
    del executable
    pyworkbench = importlib.import_module("ansys.workbench.core")
    launcher = cast(Callable[..., Any], pyworkbench.launch_workbench)
    client: Any = None
    launcher_process_id: int | None = None
    try:
        client = launcher(
            show_gui=False,
            version="261",
            client_workdir=str(workdir),
            server_workdir=str(workdir),
        )
        launcher_process_id = int(client._launcher._process_id)
        messages = client.run_script_string(
            "import json\nwb_script_result=json.dumps([m.Summary for m in GetMessages()])"
        )
        return {
            "server_version": str(client.server_version),
            "messages": messages,
        }
    finally:
        if client is not None:
            with suppress(Exception):
                client.exit()
            if launcher_process_id is not None and not _process_exited(launcher_process_id):
                with suppress(Exception):
                    client._launcher.exit()
                if not _process_exited(launcher_process_id):
                    _terminate_owned_tree(launcher_process_id)
                if not _process_exited(launcher_process_id, timeout_seconds=1):
                    raise RuntimeError(
                        f"Owned Workbench launcher did not exit: {launcher_process_id}"
                    )


def execute(kind: str, target: str, workdir: Path) -> dict[str, object]:
    """Execute one isolated probe."""

    if kind == "package":
        return _probe_package(target)
    executable = Path(target).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    if kind == "mechanical":
        return _probe_mechanical(executable, workdir)
    if kind == "mechanical_embedding":
        return _probe_mechanical_embedding(workdir)
    if kind == "workbench":
        return _probe_workbench(executable, workdir)
    raise ValueError(f"Unknown probe kind: {kind}")


def main(argv: list[str] | None = None) -> int:
    """Run one probe and print exactly one structured result."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "kind", choices=("package", "mechanical", "mechanical_embedding", "workbench")
    )
    parser.add_argument("target")
    parser.add_argument("--workdir", type=Path)
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(os.environ.get("ANSYS_RESEARCH_RUNTIME", "runtime")) / "probes"
    try:
        result: dict[str, Any] = {
            "ok": True,
            "details": execute(args.kind, args.target, workdir.resolve()),
        }
        exit_code = 0
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        exit_code = 2
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
