"""Production queue-drainer command-line boundary tests."""

from __future__ import annotations

import pytest

from ansys_research_runner.adapters.worker import queue_dispatcher


def test_worker_help_does_not_drain_queue(monkeypatch, capsys) -> None:
    def unexpected_drain(**_kwargs):
        raise AssertionError("help must not touch the Job Registry")

    monkeypatch.setattr(queue_dispatcher, "run_production_worker_once", unexpected_drain)

    with pytest.raises(SystemExit) as exit_info:
        queue_dispatcher.main(["--help"])

    assert exit_info.value.code == 0
    assert "concurrency one" in capsys.readouterr().out
