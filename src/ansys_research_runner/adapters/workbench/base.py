"""Minimal injectable contracts for the reviewed PyWorkbench adapter."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class WorkbenchClient(Protocol):
    """Public methods consumed by the G7 adapter."""

    server_version: int

    def run_script_string(self, script_string: str, *, log_level: str = "error") -> Any:
        """Execute one adapter-owned reviewed script."""
        ...

    def download_project_archive(
        self,
        archive_name: str,
        *,
        include_solution_result_files: bool = True,
        show_progress: bool = True,
    ) -> None:
        """Create and download a Workbench project archive."""
        ...

    def start_mechanical_server(self, system_name: str, port: int = 0) -> int:
        """Start the official Mechanical service for a project system."""
        ...

    def stop_mechanical_server(self, system_name: str) -> None:
        """Stop the Mechanical service for a project system."""
        ...

    def exit(self) -> None:
        """Request normal Workbench shutdown."""
        ...


class MechanicalClient(Protocol):
    """Small PyMechanical surface needed to prove a Workbench handoff."""

    @property
    def project_directory(self) -> str:
        """Return the server-side Mechanical project directory."""
        ...

    @property
    def is_alive(self) -> bool:
        """Return whether the service responds."""
        ...

    @property
    def version(self) -> Any:
        """Return the connected Mechanical version."""
        ...

    def get_product_info(self) -> Any:
        """Return official product information."""
        ...

    def run_python_script(
        self,
        script_block: str,
        enable_logging: bool = False,
        log_level: str = "WARNING",
        progress_interval: int = 2000,
        python_api_version: int = -1,
    ) -> Any:
        """Execute one adapter-owned reviewed Mechanical script."""
        ...

    def upload(
        self,
        file_name: str,
        file_location_destination: str | None = None,
        chunk_size: int = 1048576,
        progress_bar: bool = True,
    ) -> str:
        """Upload one run-owned input to the Mechanical project directory."""
        ...

    def download(
        self,
        files: str | list[str],
        target_dir: str | None = None,
        chunk_size: int = 262144,
        progress_bar: bool | None = None,
        recursive: bool = False,
    ) -> list[str]:
        """Download reviewed result files from the Mechanical project directory."""
        ...

    def exit(self, force: bool = False) -> None:
        """Close the Mechanical connection or process."""
        ...


WorkbenchFactory = Callable[..., WorkbenchClient]
MechanicalConnector = Callable[..., MechanicalClient]


class WorkbenchCouplingAdapter(Protocol):
    """Contract test boundary for an optional Workbench coupling probe."""

    def execute(self, workdir: Path) -> dict[str, object]:
        """Run one bounded lifecycle and return structured capability evidence."""
        ...
