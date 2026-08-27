"""Internal G6 fault-injection worker; never selected by production run requests."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

from ansys_research_runner.io import atomic_write_text


def _emit(control: Path, event_type: str, **payload: object) -> None:
    control.mkdir(parents=True, exist_ok=True)
    with (control / "events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"type": event_type, **payload}, sort_keys=True) + "\n")
        stream.flush()


def _heartbeat(control: Path) -> None:
    atomic_write_text(control / "heartbeat.txt", f"{time.time():.9f}\n")


def _wait_with_heartbeat(control: Path, duration_s: float, interval_s: float) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        _heartbeat(control)
        time.sleep(interval_s)


def run(job_dir: Path, behavior: str, duration_s: float, interval_s: float) -> int:
    """Execute one deterministic dummy behavior for supervisor tests."""

    control = job_dir / "control"
    artifacts = job_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    _emit(control, "phase", status="SOLVING", detail="dummy solve entered")
    _heartbeat(control)
    print(f"dummy worker behavior={behavior}", flush=True)

    if behavior == "crash_during_solve":
        time.sleep(0.05)
        return 17
    if behavior == "self_kill":
        os.kill(os.getpid(), signal.SIGTERM)
        return 19
    if behavior == "heartbeat_stop":
        time.sleep(duration_s)
        return 20
    if behavior in {"wall_timeout", "wait_for_cancel"}:
        _wait_with_heartbeat(control, duration_s, interval_s)
        return 0
    if behavior == "partial_artifact_failure":
        atomic_write_text(artifacts / "partial.txt", "preserved partial evidence\n")
        _heartbeat(control)
        time.sleep(0.05)
        return 23
    if behavior != "normal":
        print(f"unsupported behavior: {behavior}", file=sys.stderr, flush=True)
        return 64

    atomic_write_text(artifacts / "temperature-summary.json", '{"maximum_K":333.15}\n')
    _wait_with_heartbeat(control, min(duration_s, 0.1), interval_s)
    _emit(control, "phase", status="POSTPROCESSING", detail="dummy postprocess entered")
    _heartbeat(control)
    _emit(control, "phase", status="EXPORTING", detail="dummy export entered")
    _heartbeat(control)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the internal worker from an isolated Python process."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument(
        "--behavior",
        choices=(
            "normal",
            "crash_during_solve",
            "self_kill",
            "heartbeat_stop",
            "wall_timeout",
            "wait_for_cancel",
            "partial_artifact_failure",
        ),
        required=True,
    )
    parser.add_argument("--duration-s", type=float, default=0.2)
    parser.add_argument("--interval-s", type=float, default=0.02)
    args = parser.parse_args(argv)
    return run(args.job_dir.resolve(), args.behavior, args.duration_s, args.interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
