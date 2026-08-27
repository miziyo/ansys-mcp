"""Conservative PID/create-time process ownership and cleanup."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psutil

from ansys_research_runner.domain.jobs import OwnedProcessRecord, ResourceSnapshot
from ansys_research_runner.services.job_registry import JobRegistry


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _command_fingerprint(arguments: Sequence[str]) -> str:
    payload = "\0".join(arguments).encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Evidence from one conservative termination attempt."""

    graceful_stop_attempted: bool
    matched: tuple[int, ...]
    identity_mismatches: tuple[int, ...]
    already_exited: tuple[int, ...]
    terminated: tuple[int, ...]
    killed: tuple[int, ...]
    access_denied: tuple[int, ...]
    remaining: tuple[int, ...]


class ProcessSupervisor:
    """Track and terminate only processes proven to belong to a job."""

    def __init__(self, registry: JobRegistry) -> None:
        self._registry = registry

    def register_spawned_process(
        self,
        job_id: str,
        pid: int,
        *,
        launcher_pid: int | None = None,
        discovery_method: str = "direct_spawn",
    ) -> OwnedProcessRecord:
        """Snapshot a newly spawned process before it can be considered owned."""

        process = psutil.Process(pid)
        with process.oneshot():
            create_time = process.create_time()
            parent_pid = process.ppid()
            try:
                executable = process.exe()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                executable = ""
            try:
                arguments = process.cmdline()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                arguments = []
        record = OwnedProcessRecord(
            job_id=job_id,
            pid=pid,
            parent_pid=parent_pid,
            create_time=create_time,
            executable_path=executable,
            command_line_fingerprint=_command_fingerprint(arguments),
            launcher_pid=launcher_pid or pid,
            discovery_method=discovery_method,
            registered_at=_utc_now(),
        )
        self._registry.register_process(record)
        return record

    def discover_descendants(self, job_id: str) -> tuple[OwnedProcessRecord, ...]:
        """Register descendants of matching owned roots, preserving ancestry evidence."""

        discovered: list[OwnedProcessRecord] = []
        roots = self._registry.list_owned_processes(job_id, include_ended=False)
        existing = {(record.pid, record.create_time) for record in roots}
        for root in roots:
            process = self._matching_process(root)
            if process is None:
                continue
            try:
                children = process.children(recursive=True)
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            for child in children:
                try:
                    identity = (child.pid, child.create_time())
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                if identity in existing:
                    continue
                try:
                    record = self.register_spawned_process(
                        job_id,
                        child.pid,
                        launcher_pid=root.launcher_pid,
                        discovery_method=f"descendant_of:{root.pid}",
                    )
                except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                existing.add(identity)
                discovered.append(record)
        return tuple(discovered)

    def terminate_owned(
        self,
        job_id: str,
        *,
        graceful_stop: Callable[[], None] | None = None,
        graceful_timeout_s: float = 0.5,
        kill_timeout_s: float = 1.0,
    ) -> CleanupReport:
        """Stop matching owned processes leaf-first, then kill only survivors."""

        graceful_attempted = graceful_stop is not None
        if graceful_stop is not None:
            with suppress(Exception):  # cleanup continues after adapter stop failure
                graceful_stop()
            time.sleep(min(graceful_timeout_s, 0.1))

        self.discover_descendants(job_id)
        records = self._registry.list_owned_processes(job_id, include_ended=False)
        by_pid = {record.pid: record for record in records}
        matched: list[int] = []
        mismatches: list[int] = []
        already_exited: list[int] = []
        denied: list[int] = []
        processes: list[psutil.Process] = []
        for record in records:
            try:
                process = psutil.Process(record.pid)
                actual_create_time = process.create_time()
            except psutil.NoSuchProcess:
                already_exited.append(record.pid)
                self._registry.mark_process_ended(
                    job_id, record.pid, record.create_time, result="already_exited"
                )
                continue
            except (psutil.AccessDenied, psutil.ZombieProcess):
                denied.append(record.pid)
                continue
            if abs(actual_create_time - record.create_time) > 0.01:
                mismatches.append(record.pid)
                continue
            matched.append(record.pid)
            processes.append(process)

        def depth(process: psutil.Process) -> int:
            value = 0
            current_record: OwnedProcessRecord | None = by_pid.get(process.pid)
            visited: set[int] = set()
            while current_record is not None and current_record.parent_pid in by_pid:
                if current_record.pid in visited:
                    break
                visited.add(current_record.pid)
                value += 1
                current_record = by_pid.get(current_record.parent_pid or -1)
            return value

        processes.sort(key=depth, reverse=True)
        requested: list[psutil.Process] = []
        for process in processes:
            try:
                process.terminate()
                requested.append(process)
            except psutil.NoSuchProcess:
                already_exited.append(process.pid)
            except (psutil.AccessDenied, psutil.ZombieProcess):
                denied.append(process.pid)
        gone, alive = psutil.wait_procs(requested, timeout=graceful_timeout_s)
        terminated = [process.pid for process in gone]
        killed: list[int] = []
        kill_requested: list[psutil.Process] = []
        for process in alive:
            record = by_pid[process.pid]
            if self._matching_process(record) is None:
                mismatches.append(process.pid)
                continue
            try:
                process.kill()
                kill_requested.append(process)
            except psutil.NoSuchProcess:
                terminated.append(process.pid)
            except (psutil.AccessDenied, psutil.ZombieProcess):
                denied.append(process.pid)
        killed_gone, killed_alive = psutil.wait_procs(kill_requested, timeout=kill_timeout_s)
        killed.extend(process.pid for process in killed_gone)
        remaining = [process.pid for process in killed_alive if process.is_running()]

        for process_id in {*terminated, *killed, *already_exited}:
            ended_record = by_pid.get(process_id)
            if ended_record is None:
                continue
            result = (
                "killed"
                if process_id in killed
                else "terminated"
                if process_id in terminated
                else "already_exited"
            )
            self._registry.mark_process_ended(
                job_id, ended_record.pid, ended_record.create_time, result=result
            )
        return CleanupReport(
            graceful_stop_attempted=graceful_attempted,
            matched=tuple(sorted(set(matched))),
            identity_mismatches=tuple(sorted(set(mismatches))),
            already_exited=tuple(sorted(set(already_exited))),
            terminated=tuple(sorted(set(terminated))),
            killed=tuple(sorted(set(killed))),
            access_denied=tuple(sorted(set(denied))),
            remaining=tuple(sorted(set(remaining))),
        )

    def resource_snapshot(self, job_id: str, workdir: Path) -> ResourceSnapshot:
        """Return best-effort process and working-directory resource usage."""

        rss = 0
        cpu = 0.0
        count = 0
        for record in self._registry.list_owned_processes(job_id, include_ended=False):
            process = self._matching_process(record)
            if process is None:
                continue
            try:
                memory = process.memory_info().rss
                times = process.cpu_times()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            rss += memory
            cpu += times.user + times.system
            count += 1
        disk = 0
        if workdir.is_dir():
            for path in workdir.rglob("*"):
                try:
                    if path.is_file():
                        disk += path.stat().st_size
                except OSError:
                    continue
        return ResourceSnapshot(
            rss_bytes=rss,
            cpu_time_s=cpu,
            workdir_bytes=disk,
            observed_processes=count,
        )

    @staticmethod
    def _matching_process(record: OwnedProcessRecord) -> psutil.Process | None:
        try:
            process = psutil.Process(record.pid)
            if abs(process.create_time() - record.create_time) > 0.01:
                return None
            return process
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
