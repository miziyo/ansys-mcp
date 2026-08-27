"""G1 capability service tests."""

from __future__ import annotations

import json
import sys

from typer.testing import CliRunner

from ansys_research_runner.cli import app, main
from ansys_research_runner.domain.capabilities import (
    CapabilityReport,
    CapabilityStatus,
    HostCapability,
)
from ansys_research_runner.services.capability_service import (
    discover_products,
    persist_capabilities,
    probe_package,
    resolve_ansys_root,
    run_child_probe,
)


def test_discover_products_uses_supplied_root(tmp_path) -> None:
    executable = tmp_path / "aisol" / "bin" / "winx64" / "AnsysWBU.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    products = {item.product: item for item in discover_products(tmp_path)}
    assert products["mechanical"].installed is True
    assert products["workbench"].installed is False


def test_resolve_ansys_root_honors_explicit_environment(tmp_path) -> None:
    assert resolve_ansys_root({"ANSYS_RESEARCH_ANSYS_ROOT": str(tmp_path)}) == tmp_path.resolve()


def test_missing_package_is_unavailable() -> None:
    capability = probe_package("definitely-not-an-installed-distribution", "does.not.exist")
    assert capability.status is CapabilityStatus.UNAVAILABLE
    assert capability.reason == "PACKAGE_NOT_INSTALLED"


def test_child_probe_timeout_is_classified() -> None:
    status, details, reason = run_child_probe(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.05,
    )
    assert status is CapabilityStatus.TIMED_OUT
    assert reason == "PROBE_TIMEOUT"
    assert "stderr_tail" in details


def test_invalid_child_probe_output_retains_cleanup_evidence() -> None:
    status, details, reason = run_child_probe(
        [sys.executable, "-c", "print('native process exited before JSON')"],
        timeout_seconds=2,
    )

    assert status is CapabilityStatus.ERROR
    assert reason == "INVALID_PROBE_OUTPUT"
    cleanup = details["owned_process_cleanup"]
    assert isinstance(cleanup, dict)
    assert cleanup["remaining"] == []
    assert cleanup["terminated"] == []
    assert cleanup["killed"] == []
    assert cleanup["already_exited"] == cleanup["observed"]


def test_static_doctor_report_does_not_overwrite_tracked_live_evidence(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ANSYS_RESEARCH_ROOT", str(tmp_path))
    target = tmp_path / "docs" / "capabilities.md"
    target.parent.mkdir(parents=True)
    target.write_text("verified live evidence\n", encoding="utf-8")
    report = CapabilityReport(
        generated_at="2026-08-23T00:00:00Z",
        host=HostCapability(
            os="Windows",
            os_release="11",
            python="3.12.14",
            cpu_count=1,
            memory_bytes=1,
            ansys_root=tmp_path,
            runtime_writable=True,
        ),
        packages=[],
        products=[],
        required_mechanical_live=CapabilityStatus.NOT_PROBED,
    )

    persist_capabilities(report)

    assert target.read_text(encoding="utf-8") == "verified live evidence\n"
    assert (tmp_path / "runtime" / "capability_report.json").is_file()


def test_doctor_live_returns_blocked_exit_code(monkeypatch, tmp_path) -> None:
    report = CapabilityReport(
        generated_at="2026-08-23T00:00:00Z",
        host=HostCapability(
            os="Windows",
            os_release="11",
            python="3.12.14",
            cpu_count=1,
            memory_bytes=1,
            ansys_root=tmp_path,
            runtime_writable=True,
        ),
        packages=[],
        products=[],
        required_mechanical_live=CapabilityStatus.BLOCKED,
    )
    monkeypatch.setattr(
        "ansys_research_runner.services.application_service.collect_capabilities",
        lambda **_: report,
    )
    monkeypatch.setattr(
        "ansys_research_runner.services.application_service.persist_capabilities",
        lambda _: None,
    )
    result = CliRunner().invoke(app, ["doctor", "--live", "--json"])
    assert result.exit_code == 3
    assert json.loads(result.stdout)["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert main(["doctor", "--live", "--json"]) == 3
