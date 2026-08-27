"""Fail closed when a public release tree contains private or generated material."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DENIED_PREFIXES = (
    ".cache/",
    ".tmp/",
    ".venv",
    "artifacts/",
    "dist/",
    "runtime/",
    "script_templates/",
    "tests/legacy/",
    "tutorials/",
    "workspace/",
)
_DENIED_NAMES = frozenset({".env", "id_rsa", "id_ed25519"})
_ALLOWED_PROJECT_ASSETS = frozenset(
    {
        "src/ansys_research_runner/resources/geometry/g3_box.step",
        "src/ansys_research_runner/resources/geometry/g3_cylinder.step",
    }
)
_DENIED_MODEL_SUFFIXES = (
    ".parasolid",
    ".pmdb",
    ".scdoc",
    ".scdocx",
    ".stp",
    ".x_b",
    ".x_t",
)
_DENIED_SUFFIXES = (
    ".aedt",
    ".agdb",
    ".cdb",
    ".cas",
    ".cas.h5",
    ".dat",
    ".dat.h5",
    ".dsco",
    ".glb",
    ".mechdb",
    ".rst",
    ".rth",
    ".twin",
    ".wbpj",
    ".wbpz",
)
_ALLOWED_COMMIT_EMAILS = frozenset(
    {
        "noreply" + "@github.com",
        "74097686+miziyo" + "@users.noreply.github.com",
    }
)


def _secret_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    return (
        ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
        ("github_fine_grained_token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
        ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
        ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
        ("private_key", re.compile(re.escape(private_key_marker))),
        (
            "bearer_token",
            re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE),
        ),
    )


_CONTENT_PATTERNS = (
    (
        "email_address",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ),
    (
        "windows_user_profile",
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]Users[\\/][^\\/\s\"']+", re.IGNORECASE),
    ),
    ("windows_machine_name", re.compile(r"\bDESKTOP-[A-Z0-9-]{4,}\b", re.IGNORECASE)),
    ("raw_process_environment", re.compile(r"(?:^|[\"'])PATH=", re.MULTILINE)),
    (
        "workbench_user_temp",
        re.compile(r"\bWB_(?!\$\{USER_NAME\})[A-Za-z0-9_.-]+_\d+_\d+\b", re.IGNORECASE),
    ),
)


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    path: str
    line: int | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"rule": self.rule, "path": self.path}
        if self.line is not None:
            payload["line"] = self.line
        return payload


def _git(*arguments: str) -> str:
    process = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return process.stdout


def _tracked_paths() -> list[str]:
    process = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in process.stdout.split(b"\0") if item]


def _is_denied_path(path: str) -> bool:
    lowered = path.casefold()
    return (
        Path(path).name.casefold() in _DENIED_NAMES
        or any(lowered.startswith(prefix.casefold()) for prefix in _DENIED_PREFIXES)
        or lowered.endswith(
            (*_DENIED_SUFFIXES, ".pem", ".p12", ".pfx", ".key", ".sqlite", ".sqlite3")
        )
        or (
            lowered.endswith((".step", *_DENIED_MODEL_SUFFIXES))
            and lowered not in _ALLOWED_PROJECT_ASSETS
        )
        or "codex" in Path(path).name.casefold()
        or "tutorial" in Path(path).name.casefold()
    )


def _text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def audit_tree() -> tuple[list[Finding], int]:
    findings: list[Finding] = []
    scanned = 0
    patterns = (*_secret_patterns(), *_CONTENT_PATTERNS)
    for relative in _tracked_paths():
        if _is_denied_path(relative):
            findings.append(Finding("generated_or_private_path", relative))
            continue
        source = ROOT / relative
        if not source.is_file():
            continue
        content = _text(source)
        if content is None:
            continue
        scanned += 1
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule, pattern in patterns:
                if pattern.search(line):
                    findings.append(Finding(rule, relative, line_number))
    return findings, scanned


def audit_history() -> list[Finding]:
    findings: list[Finding] = []
    emails = {line.strip() for line in _git("log", "--all", "--format=%ae%n%ce").splitlines()}
    for _email in sorted(emails - _ALLOWED_COMMIT_EMAILS):
        findings.append(Finding("commit_email_not_noreply", "git-history"))
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tree-only",
        action="store_true",
        help="Skip commit metadata checks while preparing a clean public snapshot.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    findings, scanned = audit_tree()
    if not arguments.tree_only:
        findings.extend(audit_history())
    report = {
        "schema_version": 1,
        "status": "PASSED" if not findings else "FAILED",
        "tracked_files": len(_tracked_paths()),
        "text_files_scanned": scanned,
        "history_checked": not arguments.tree_only,
        "findings": [finding.as_dict() for finding in findings],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
