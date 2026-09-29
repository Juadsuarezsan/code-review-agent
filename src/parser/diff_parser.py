"""Parse unified diff text into structured per-file changes.

The parser is deliberately hand-written (no ``unidiff`` dependency at runtime) so
that partial or slightly malformed diffs pasted by a human still produce useful
output. Line numbers always refer to the *new* side of the diff, which is what
GitHub's review API expects for inline comments (``side="RIGHT"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_HUNK_RE = re.compile(
    r"^@@ -(?P<old>\d+)(?:,(?P<old_len>\d+))? \+(?P<new>\d+)(?:,(?P<new_len>\d+))? @@"
)

LANGUAGE_BY_EXTENSION: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
}


class DiffParseError(ValueError):
    """Raised when the text contains no recognizable unified-diff structure."""


@dataclass
class Hunk:
    """One ``@@`` block of a file diff."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int


@dataclass
class FileDiff:
    """Changes to one file.

    Attributes:
        path: Path of the new version (``b/`` side), or the old path for deletions.
        added: ``(new_line_no, content)`` for every ``+`` line.
        removed: ``(old_line_no, content)`` for every ``-`` line.
        new_lines: New-side line number -> content for every added or context line.
        hunks: Parsed hunk headers.
        is_new_file: True when the old side is ``/dev/null``.
        is_deleted: True when the new side is ``/dev/null``.
    """

    path: str
    added: list[tuple[int, str]] = field(default_factory=list)
    removed: list[tuple[int, str]] = field(default_factory=list)
    new_lines: dict[int, str] = field(default_factory=dict)
    hunks: list[Hunk] = field(default_factory=list)
    old_path: str | None = None
    is_new_file: bool = False
    is_deleted: bool = False

    def __post_init__(self) -> None:
        # Allow constructing with only ``added`` (used by tests and analyzers).
        for line_no, content in self.added:
            self.new_lines.setdefault(line_no, content)

    @property
    def n_added(self) -> int:
        """Number of added lines."""
        return len(self.added)

    @property
    def n_removed(self) -> int:
        """Number of removed lines."""
        return len(self.removed)

    @property
    def added_line_numbers(self) -> set[int]:
        """Set of new-side line numbers that were added."""
        return {n for n, _ in self.added}

    @property
    def language(self) -> str:
        """Language guessed from the file extension (``"unknown"`` when unmapped)."""
        return LANGUAGE_BY_EXTENSION.get(PurePosixPath(self.path).suffix.lower(), "unknown")

    @property
    def is_test_file(self) -> bool:
        """Heuristic: path lives under ``tests/`` or the basename starts with ``test_``."""
        parts = PurePosixPath(self.path).parts
        name = parts[-1] if parts else ""
        return (
            "tests" in parts[:-1]
            or "test" in parts[:-1]
            or name.startswith("test_")
            or name.endswith("_test.py")
        )

    @property
    def new_content(self) -> str:
        """New-side lines joined in order (added + context), without padding."""
        return "\n".join(self.new_lines[k] for k in sorted(self.new_lines)) + (
            "\n" if self.new_lines else ""
        )

    def new_snippet(self) -> str:
        """Reconstruct the visible new-side text with blank-line padding.

        Line *i* of the returned text is new-file line *i* when it is visible in the
        diff and an empty line otherwise, so line numbers reported by tools that run
        on this text (ruff, tree-sitter, ``ast``) match real file line numbers.
        """
        if not self.new_lines:
            return ""
        last = max(self.new_lines)
        return "\n".join(self.new_lines.get(i, "") for i in range(1, last + 1)) + "\n"


def _path_from_git_header(line: str) -> str:
    # "diff --git a/path b/path" — take the b/ side; paths may contain spaces.
    marker = " b/"
    idx = line.rfind(marker)
    if idx == -1:
        return line.split()[-1]
    return line[idx + len(marker) :].strip()


def _clean_path(raw: str) -> str:
    raw = raw.strip()
    if "\t" in raw:
        raw = raw.split("\t", 1)[0]
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def parse_unified_diff(diff: str) -> list[FileDiff]:
    """Parse a unified diff into :class:`FileDiff` objects.

    Accepts ``git diff`` output (with ``diff --git`` headers) as well as plain
    ``---``/``+++`` diffs. Binary files are skipped. Returns an empty list for
    empty input.
    """
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    old_line = 0
    in_hunk = False
    # Remaining lines declared by the current hunk header; the hunk closes when both hit 0,
    # so a following "--- a/other" (plain diff without "diff --git") is a header, not a removal.
    old_left = 0
    new_left = 0

    def flush() -> None:
        nonlocal current
        if current is not None and (current.hunks or current.added or current.removed):
            files.append(current)
        current = None

    for raw in diff.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git"):
            flush()
            current = FileDiff(path=_path_from_git_header(line))
            in_hunk = False
            continue
        if line.startswith("Binary files"):
            current = None
            in_hunk = False
            continue
        if line.startswith("--- ") and not in_hunk:
            if current is not None and current.hunks:
                flush()  # plain diff without "diff --git": a new file starts here
            if current is None:
                current = FileDiff(path="<unknown>")
            target = line[4:]
            if target.strip() == "/dev/null":
                current.is_new_file = True
            else:
                current.old_path = _clean_path(target)
            continue
        if line.startswith("+++ ") and not in_hunk:
            if current is None:
                current = FileDiff(path="<unknown>")
            target = line[4:]
            if target.strip() == "/dev/null":
                current.is_deleted = True
                if current.old_path:
                    current.path = current.old_path
            else:
                current.path = _clean_path(target)
            continue
        m = _HUNK_RE.match(line)
        if m:
            if current is None:
                current = FileDiff(path="<unknown>")
            hunk = Hunk(
                old_start=int(m.group("old")),
                old_len=int(m.group("old_len") or 1),
                new_start=int(m.group("new")),
                new_len=int(m.group("new_len") or 1),
            )
            current.hunks.append(hunk)
            new_line = hunk.new_start - 1
            old_line = hunk.old_start - 1
            old_left, new_left = hunk.old_len, hunk.new_len
            in_hunk = True
            continue
        if current is None or not in_hunk:
            continue
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("+"):
            new_line += 1
            new_left -= 1
            current.added.append((new_line, line[1:]))
            current.new_lines[new_line] = line[1:]
        elif line.startswith("-"):
            old_line += 1
            old_left -= 1
            current.removed.append((old_line, line[1:]))
        elif line.startswith(" ") or line == "":
            new_line += 1
            old_line += 1
            new_left -= 1
            old_left -= 1
            current.new_lines[new_line] = line[1:] if line else ""
        else:
            # Anything else ends the hunk (e.g. "index ..." of the next file without header).
            in_hunk = False
        if in_hunk and old_left <= 0 and new_left <= 0:
            in_hunk = False
    flush()
    return files


def parse_unified_diff_strict(diff: str) -> list[FileDiff]:
    """Like :func:`parse_unified_diff` but raise :class:`DiffParseError` when nothing parses."""
    if not diff.strip():
        raise DiffParseError("Diff is empty.")
    files = parse_unified_diff(diff)
    if not files:
        raise DiffParseError("No unified-diff hunks found (expected '@@ -a,b +c,d @@' headers).")
    return files
