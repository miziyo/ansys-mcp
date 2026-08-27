"""Host and official API capability discovery."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Final

import psutil

from ansys_research_runner.config import RunnerPaths
from ansys_research_runner.domain.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    HostCapability,
    PackageCapability,
    ProductCapability,
)
from ansys_research_runner.installation import resolve_ansys_installation
from ansys_research_runner.io import atomic_write_json

PACKAGE_MODULES: Final[dict[str, str]] = {
    "ansys-workbench-core": "ansys.workbench.core",
    "ansys-mechanical-core": "ansys.mechanical.core",
    "ansys-dpf-core": "ansys.dpf.core",
    "ansys-geometry-core": "ansys.geometry.core",
    "ansys-meshing-prime": "ansys.meshing.prime",
    "ansys-common-mcp": "ansys.common.mcp",
}

PRODUCT_PATHS: Final[dict[str, Path]] = {
    "workbench": Path("Framework/bin/Win64/RunWB2.exe"),
    "mechanical": Path("aisol/bin/winx64/AnsysWBU.exe"),
    "discovery": Path("Discovery/Discovery.exe"),
    "spaceclaim": Path("scdm/SpaceClaim.exe"),
}


def utc_now() -> str:
    """Return a stable UTC timestamp."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def resolve_ansys_root(environment: Mapping[str, str] | None = None) -> Path:
    """Resolve the newest local Ansys installation without launching it."""

    return resolve_ansys_installation(environment)


def _terminate_probe(process: subprocess.Popen[str]) -> None:
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in reversed(children):
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=5)
        for item in alive:
            item.kill()
    except (psutil.Error, ProcessLookupError):
        process.kill()


def _observe_descendants(process: subprocess.Popen[str], observed: dict[int, float]) -> None:
    """Record exact descendant identities while the probe parent is alive."""

    try:
        descendants = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        return
    for descendant in descendants:
        try:
            observed.setdefault(descendant.pid, descendant.create_time())
        except psutil.Error:
            continue


def _cleanup_observed_descendants(observed: Mapping[int, float]) -> dict[str, object]:
    """Remove surviving processes whose PID and create time were observed as descendants."""

    surviving: list[psutil.Process] = []
    already_exited: list[int] = []
    for process_id, create_time in reversed(list(observed.items())):
        try:
            process = psutil.Process(process_id)
            if abs(process.create_time() - create_time) > 0.01:
                continue
            surviving.append(process)
        except psutil.Error:
            already_exited.append(process_id)

    terminated: list[int] = []
    for process in surviving:
        try:
            process.terminate()
            terminated.append(process.pid)
        except psutil.Error:
            continue
    _, alive = psutil.wait_procs(surviving, timeout=5)
    killed: list[int] = []
    for process in alive:
        try:
            process.kill()
            killed.append(process.pid)
        except psutil.Error:
            continue
    _, still_alive = psutil.wait_procs(alive, timeout=2)
    return {
        "observed": sorted(observed),
        "already_exited": sorted(already_exited),
        "terminated": sorted(terminated),
        "killed": sorted(killed),
        "remaining": sorted(process.pid for process in still_alive),
    }


def run_child_probe(
    argv: Sequence[str], *, timeout_seconds: float
) -> tuple[CapabilityStatus, dict[str, object], str | None]:
    """Run a bounded probe and classify its structured output."""

    process = subprocess.Popen(
        list(argv),
        cwd=RunnerPaths.from_environment().root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    observed: dict[int, float] = {}
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        _observe_descendants(process, observed)
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(0.1)
    if timed_out:
        _observe_descendants(process, observed)
        _terminate_probe(process)
    stdout, stderr = process.communicate()
    cleanup = _cleanup_observed_descendants(observed)
    if timed_out:
        return (
            CapabilityStatus.TIMED_OUT,
            {
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
                "owned_process_cleanup": cleanup,
            },
            "PROBE_TIMEOUT",
        )

    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:
        return (
            CapabilityStatus.ERROR,
            {
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
                "owned_process_cleanup": cleanup,
            },
            "INVALID_PROBE_OUTPUT",
        )
    if process.returncode == 0 and payload.get("ok") is True:
        details = payload.get("details", {})
        structured = details if isinstance(details, dict) else {}
        structured["owned_process_cleanup"] = cleanup
        return CapabilityStatus.AVAILABLE, structured, None
    reason = str(payload.get("error") or stderr[-1000:] or "Probe failed")
    details = {
        "error_type": payload.get("error_type"),
        "traceback": payload.get("traceback"),
        "stderr_tail": stderr[-4000:],
        "owned_process_cleanup": cleanup,
    }
    return CapabilityStatus.BLOCKED, details, reason


def probe_package(
    distribution: str, module: str, *, timeout_seconds: float = 20
) -> PackageCapability:
    """Probe distribution metadata and importability in an isolated process."""

    try:
        installed_version = version(distribution)
    except PackageNotFoundError:
        return PackageCapability(
            distribution=distribution,
            module=module,
            status=CapabilityStatus.UNAVAILABLE,
            reason="PACKAGE_NOT_INSTALLED",
        )
    status, _, reason = run_child_probe(
        [
            sys.executable,
            "-m",
            "ansys_research_runner.adapters.capability.probe_worker",
            "package",
            module,
        ],
        timeout_seconds=timeout_seconds,
    )
    return PackageCapability(
        distribution=distribution,
        module=module,
        version=installed_version,
        status=status,
        reason=reason,
    )


def discover_products(ansys_root: Path) -> list[ProductCapability]:
    """Return deterministic product installation records."""

    return [
        ProductCapability(
            product=product,
            executable=(ansys_root / relative).resolve(),
            installed=(ansys_root / relative).is_file(),
        )
        for product, relative in sorted(PRODUCT_PATHS.items())
    ]


def collect_capabilities(*, live: bool, probe_timeout_seconds: float = 180) -> CapabilityReport:
    """Collect static capabilities and optionally launch required products."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    ansys_root = resolve_ansys_root()
    packages = [
        probe_package(distribution, module)
        for distribution, module in sorted(PACKAGE_MODULES.items())
    ]
    products = discover_products(ansys_root)
    live_products: list[ProductCapability] = []
    for product in products:
        if not live or product.product not in {"mechanical", "workbench"}:
            live_products.append(product)
            continue
        if not product.installed:
            live_products.append(
                product.model_copy(
                    update={
                        "live_status": CapabilityStatus.UNAVAILABLE,
                        "reason": "EXECUTABLE_NOT_FOUND",
                    }
                )
            )
            continue
        status, details, reason = run_child_probe(
            [
                sys.executable,
                "-m",
                "ansys_research_runner.adapters.capability.probe_worker",
                product.product,
                str(product.executable),
                "--workdir",
                str(paths.runtime / "probes" / product.product),
            ],
            timeout_seconds=probe_timeout_seconds,
        )
        live_products.append(
            product.model_copy(update={"live_status": status, "details": details, "reason": reason})
        )

    mechanical = next(item for item in live_products if item.product == "mechanical")
    required_status = mechanical.live_status if live else CapabilityStatus.NOT_PROBED
    return CapabilityReport(
        generated_at=utc_now(),
        host=HostCapability(
            os=platform.system(),
            os_release=platform.release(),
            python=platform.python_version(),
            cpu_count=psutil.cpu_count(logical=True) or 1,
            memory_bytes=psutil.virtual_memory().total,
            ansys_root=ansys_root,
            runtime_writable=os.access(paths.runtime, os.W_OK),
        ),
        packages=packages,
        products=live_products,
        required_mechanical_live=required_status,
    )


def persist_capabilities(report: CapabilityReport) -> None:
    """Persist machine-local capability evidence under the ignored runtime root."""

    paths = RunnerPaths.from_environment()
    paths.ensure_runtime()
    atomic_write_json(
        paths.runtime / "capability_report.json",
        report.model_dump(mode="json"),
    )
    if report.required_mechanical_live in {CapabilityStatus.BLOCKED, CapabilityStatus.TIMED_OUT}:
        mechanical = next(item for item in report.products if item.product == "mechanical")
        atomic_write_json(
            paths.blockers / "G1.json",
            {
                "gate": "G1",
                "status": "BLOCKED_ENVIRONMENT",
                "failed_api": "ansys.mechanical.core.launch_mechanical",
                "ansys_version": "261",
                "package_versions": {
                    item.distribution: item.version for item in report.packages if item.version
                },
                "exception": mechanical.reason,
                "details": mechanical.details,
                "reproduction_command": "ansys-research doctor --live --json",
            },
        )
