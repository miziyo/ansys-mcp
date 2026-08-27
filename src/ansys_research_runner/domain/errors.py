"""Structured, machine-readable domain failures."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable error codes exposed by validation and resolution services."""

    UNIT_REQUIRED = "UNIT_REQUIRED"
    UNIT_DIMENSION_MISMATCH = "UNIT_DIMENSION_MISMATCH"
    UNIT_NORMALIZATION_MISMATCH = "UNIT_NORMALIZATION_MISMATCH"
    TEMPERATURE_KIND_MISMATCH = "TEMPERATURE_KIND_MISMATCH"
    INVALID_COORDINATE_FRAME = "INVALID_COORDINATE_FRAME"
    AMBIGUOUS_COORDINATE_FRAME = "AMBIGUOUS_COORDINATE_FRAME"
    INVALID_SELECTOR = "INVALID_SELECTOR"
    UNSUPPORTED_SELECTOR_CAPABILITY = "UNSUPPORTED_SELECTOR_CAPABILITY"
    UNRESOLVED_ROLE = "UNRESOLVED_ROLE"
    AMBIGUOUS_ROLE = "AMBIGUOUS_ROLE"
    INVALID_CARDINALITY = "INVALID_CARDINALITY"
    ROLE_DEPENDENCY_CYCLE = "ROLE_DEPENDENCY_CYCLE"
    UNKNOWN_ROLE = "UNKNOWN_ROLE"
    PREFLIGHT_VALIDATION_FAILED = "PREFLIGHT_VALIDATION_FAILED"
    GEOMETRY_CAPABILITY_MISSING = "GEOMETRY_CAPABILITY_MISSING"
    SOLVER_CAPABILITY_MISSING = "SOLVER_CAPABILITY_MISSING"
    SOURCE_MODEL_MISMATCH = "SOURCE_MODEL_MISMATCH"
    PATH_OUTSIDE_ROOT = "PATH_OUTSIDE_ROOT"
    PATH_TRAVERSAL = "PATH_TRAVERSAL"
    SYMLINK_ESCAPE = "SYMLINK_ESCAPE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    YAML_TOO_LARGE = "YAML_TOO_LARGE"
    YAML_UNSAFE = "YAML_UNSAFE"
    CONTRACT_INVALID = "CONTRACT_INVALID"
    ARBITRARY_SCRIPT_FIELD = "ARBITRARY_SCRIPT_FIELD"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_CONFLICT = "JOB_CONFLICT"
    JOB_STATE_INVALID = "JOB_STATE_INVALID"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DomainError(ValueError):
    """Domain failure carrying a stable code, input path, and contextual details."""

    def __init__(
        self,
        code: ErrorCode,
        path: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible error payload."""

        return {
            "code": self.code.value,
            "path": self.path,
            "message": self.message,
            "details": self.details,
        }
