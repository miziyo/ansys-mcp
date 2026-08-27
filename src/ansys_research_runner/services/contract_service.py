"""Safe loading and deterministic serialization of public contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from ansys_research_runner.domain.errors import DomainError, ErrorCode

_FORBIDDEN_EXECUTION_FIELDS = frozenset(
    {
        "code",
        "command",
        "commands",
        "eval",
        "exec",
        "ironpython",
        "python",
        "python_code",
        "script",
        "script_file",
        "shell",
        "shell_command",
    }
)


def load_yaml_contract[ContractT: BaseModel](
    path: Path,
    model_type: type[ContractT],
    *,
    maximum_bytes: int = 1_048_576,
) -> ContractT:
    """Safely load a bounded YAML document into a strict Pydantic contract."""

    if path.stat().st_size > maximum_bytes:
        raise ValueError(f"YAML document exceeds {maximum_bytes} bytes.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def _forbidden_field(value: Any, path: str = "document") -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if key_text.lower() in _FORBIDDEN_EXECUTION_FIELDS:
                return child_path
            found = _forbidden_field(child, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found = _forbidden_field(child, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _location_text(location: tuple[int | str, ...]) -> str:
    output = ""
    for item in location:
        if isinstance(item, int):
            output += f"[{item}]"
        else:
            output += ("." if output else "") + item
    return output or "document"


def _contract_validation_error(error: ValidationError) -> DomainError:
    entries = error.errors(include_url=False)
    for entry in entries:
        context = entry.get("ctx")
        cause = context.get("error") if isinstance(context, dict) else None
        if isinstance(cause, DomainError):
            return DomainError(
                cause.code,
                _location_text(tuple(entry["loc"])),
                cause.message,
                details=cause.details,
            )
    if any("selector" in tuple(str(item).lower() for item in entry["loc"]) for entry in entries):
        code = ErrorCode.INVALID_SELECTOR
        message = "Selector expression is not part of the supported data-only DSL."
    else:
        code = ErrorCode.CONTRACT_INVALID
        message = "Document does not satisfy its versioned contract."
    details = {
        "issues": [
            {
                "type": str(entry["type"]),
                "path": _location_text(tuple(entry["loc"])),
                "message": str(entry["msg"]),
            }
            for entry in entries[:32]
        ]
    }
    first_path = details["issues"][0]["path"] if details["issues"] else "document"
    return DomainError(code, str(first_path), message, details=details)


def load_public_yaml_contract[ContractT: BaseModel](
    path: Path,
    model_type: type[ContractT],
    *,
    maximum_bytes: int = 1_048_576,
) -> ContractT:
    """Load an already path-confined public YAML file with structured failures."""

    size = path.stat().st_size
    if size > maximum_bytes:
        raise DomainError(
            ErrorCode.YAML_TOO_LARGE,
            str(path),
            f"YAML document exceeds {maximum_bytes} bytes.",
            details={"actual_bytes": size, "maximum_bytes": maximum_bytes},
        )
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DomainError(
            ErrorCode.CONTRACT_INVALID,
            str(path),
            "YAML document must be UTF-8.",
        ) from exc
    try:
        payload = yaml.safe_load(text)
    except yaml.constructor.ConstructorError as exc:
        raise DomainError(
            ErrorCode.YAML_UNSAFE,
            str(path),
            "YAML document contains an unsafe or unsupported tag.",
        ) from exc
    except yaml.YAMLError as exc:
        raise DomainError(
            ErrorCode.CONTRACT_INVALID,
            str(path),
            "YAML document is malformed.",
            details={"yaml_error": type(exc).__name__},
        ) from exc
    forbidden = _forbidden_field(payload)
    if forbidden is not None:
        raise DomainError(
            ErrorCode.ARBITRARY_SCRIPT_FIELD,
            forbidden,
            "Arbitrary script, code, or shell command fields are forbidden.",
        )
    try:
        return model_type.model_validate(payload)
    except ValidationError as exc:
        raise _contract_validation_error(exc) from exc


def deterministic_json(model: BaseModel) -> str:
    """Serialize a domain model with stable key ordering and no whitespace variance."""

    return json.dumps(
        model.model_dump(mode="json", by_alias=True),
        separators=(",", ":"),
        sort_keys=True,
    )
