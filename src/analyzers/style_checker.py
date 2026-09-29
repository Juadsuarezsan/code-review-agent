"""Style Checker: run ``ruff`` as a subprocess over the new-side snippet of each file.

The reconstructed snippet keeps real line numbers (see
:meth:`~src.parser.diff_parser.FileDiff.new_snippet`), so ruff's ``location.row``
maps directly to a reviewable line. Only findings on *added* lines are reported.
Rules that are unreliable on partial context (unused/undefined names) are ignored.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Protocol

from loguru import logger

from src.api.schemas import Category, ReviewComment, Severity
from src.parser.diff_parser import FileDiff

RUFF_SELECT = "E,W,F,B,UP,SIM,C4"
#: Not meaningful on diff fragments (names may be used/defined outside the hunk).
RUFF_IGNORE = "F401,F821,F811,F841,E501,E402,W291,W293,UP009,UP035,B018,F403,F405"
#: Codes that describe defects rather than style, mapped to the ``bug`` category.
BUG_CODES: frozenset[str] = frozenset(
    {
        "F632",
        "F502",
        "F503",
        "F504",
        "F506",
        "F507",
        "F508",
        "F509",
        "F601",
        "F602",
        "F631",
        "F701",
        "F702",
        "F704",
        "F706",
        "F707",
        "F823",
        "B002",
        "B003",
        "B004",
        "B005",
        "B006",
        "B007",
        "B009",
        "B010",
        "B012",
        "B014",
        "B015",
        "B016",
        "B017",
        "B020",
        "B021",
        "B022",
        "B023",
        "B025",
        "B029",
        "B030",
        "B031",
        "B032",
        "B033",
        "B034",
        "B035",
        "E722",
        "E902",
        "E999",
        "SIM115",  # open() without a context manager is a resource leak, not a style nit
    }
)
ERROR_CODES: frozenset[str] = frozenset(
    {"F632", "F631", "F701", "F702", "F706", "F707", "E722", "E902", "E999", "B006", "B012", "B023"}
)


class StyleCheckError(RuntimeError):
    """Raised when the linter process itself fails (not when it finds issues)."""


@dataclass(frozen=True)
class LintFinding:
    """One linter diagnostic."""

    line: int
    code: str
    message: str
    fix: str | None = None


class StyleLinter(Protocol):
    """Interface for a line-oriented linter."""

    name: str

    def lint(self, path: str, source: str) -> list[LintFinding]:
        """Lint ``source`` as if it lived at ``path``."""
        ...


class RuffLinter:
    """Run ``python -m ruff check`` on stdin with an isolated configuration."""

    name = "ruff"

    def __init__(self, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def command(self, path: str) -> list[str]:
        """Command line used for one file (exposed for tests)."""
        return [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--select",
            RUFF_SELECT,
            "--ignore",
            RUFF_IGNORE,
            "--output-format",
            "json",
            "--stdin-filename",
            path,
            "-",
        ]

    def lint(self, path: str, source: str) -> list[LintFinding]:
        """Return ruff diagnostics. Exit code 0/1 are normal; 2+ raises."""
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                self.command(path),
                input=source,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise StyleCheckError(f"ruff timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise StyleCheckError(f"cannot start ruff: {exc}") from exc
        if proc.returncode not in (0, 1):
            raise StyleCheckError(f"ruff exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        return parse_ruff_json(proc.stdout)


def parse_ruff_json(payload: str) -> list[LintFinding]:
    """Parse ruff's JSON output into :class:`LintFinding` objects."""
    if not payload.strip():
        return []
    try:
        items: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise StyleCheckError(f"ruff produced invalid JSON: {exc}") from exc
    findings: list[LintFinding] = []
    for item in items if isinstance(items, list) else []:
        fix = item.get("fix") or {}
        findings.append(
            LintFinding(
                line=int(item.get("location", {}).get("row", 0)),
                code=str(item.get("code") or "RUFF"),
                message=str(item.get("message", "")),
                fix=fix.get("message") if isinstance(fix, dict) else None,
            )
        )
    return findings


def _severity_for(code: str) -> Severity:
    if code in ERROR_CODES:
        return "error"
    if code.startswith(("F", "E7", "B")):
        return "warning"
    return "info"


def _category_for(code: str) -> Category:
    return "bug" if code in BUG_CODES else "style"


def check_style(file_diff: FileDiff, linter: StyleLinter | None = None) -> list[ReviewComment]:
    """Lint one Python file diff and return comments on added lines only.

    Linter process failures are logged and yield no comments; they never abort
    the review.
    """
    if file_diff.language != "python" or not file_diff.added:
        return []
    linter = linter or RuffLinter()
    try:
        findings = linter.lint(file_diff.path, file_diff.new_snippet())
    except StyleCheckError as exc:
        logger.error(
            "style check failed path={} linter={} err={}", file_diff.path, linter.name, exc
        )
        return []
    added = file_diff.added_line_numbers
    comments: list[ReviewComment] = []
    for f in findings:
        if f.line not in added:
            continue
        comments.append(
            ReviewComment(
                file=file_diff.path,
                line=f.line,
                severity=_severity_for(f.code),
                category=_category_for(f.code),
                body=f"{f.message} ({f.code})",
                suggestion=f.fix,
                justification=f"Reported by {linter.name} rule {f.code}.",
                rule_id=f"{linter.name}:{f.code}",
                source=linter.name,
                confidence=0.85,
            )
        )
    return comments
