"""Root-confined input path resolution for public application commands."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from ansys_research_runner.domain.errors import DomainError, ErrorCode


class InputPathPolicy:
    """Resolve user paths without permitting traversal or symlink escape."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def resolve_file(
        self,
        value: str | Path,
        *,
        base: Path | None = None,
        allowed_extensions: Iterable[str],
        path_label: str,
    ) -> Path:
        """Return a regular file confined to the configured input root."""

        raw = Path(value).expanduser()
        if ".." in raw.parts:
            raise DomainError(
                ErrorCode.PATH_TRAVERSAL,
                path_label,
                "Parent-directory traversal is forbidden in public input paths.",
                details={"value": str(value)},
            )
        active_base = (base or self.root).expanduser().resolve()
        if not active_base.is_relative_to(self.root):
            raise DomainError(
                ErrorCode.PATH_OUTSIDE_ROOT,
                path_label,
                "Input base directory is outside the configured root.",
                details={"root": str(self.root), "base": str(active_base)},
            )
        combined = raw if raw.is_absolute() else active_base / raw
        lexical = Path(os.path.abspath(combined))
        if not lexical.is_relative_to(self.root):
            raise DomainError(
                ErrorCode.PATH_OUTSIDE_ROOT,
                path_label,
                "Input path is outside the configured root.",
                details={"root": str(self.root), "value": str(value)},
            )
        resolved = lexical.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise DomainError(
                ErrorCode.SYMLINK_ESCAPE,
                path_label,
                "Input path resolves through a link outside the configured root.",
                details={"root": str(self.root), "value": str(value)},
            )
        extensions = tuple(sorted({item.lower() for item in allowed_extensions}))
        if not any(resolved.name.lower().endswith(extension) for extension in extensions):
            raise DomainError(
                ErrorCode.UNSUPPORTED_EXTENSION,
                path_label,
                "Input file extension is not supported for this command.",
                details={"allowed_extensions": list(extensions), "value": str(value)},
            )
        if not resolved.is_file():
            raise DomainError(
                ErrorCode.FILE_NOT_FOUND,
                path_label,
                "Input file does not exist or is not a regular file.",
                details={"value": str(value)},
            )
        return resolved

    def relative_text(self, path: Path) -> str:
        """Return a stable root-relative path for public evidence."""

        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise DomainError(
                ErrorCode.PATH_OUTSIDE_ROOT,
                "path",
                "Evidence path is outside the configured root.",
            )
        return resolved.relative_to(self.root).as_posix()
