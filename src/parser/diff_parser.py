"""Parse unified diff text into structured per-file changes."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileDiff:
    path: str
    added: list[tuple[int, str]] = field(default_factory=list)   # (line_no, content)
    removed: list[tuple[int, str]] = field(default_factory=list)
    new_content: str = ""

    @property
    def n_added(self) -> int:
        return len(self.added)

    @property
    def n_removed(self) -> int:
        return len(self.removed)


def parse_unified_diff(diff: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    new_line = 0
    old_line = 0
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            if current:
                files.append(current)
            # parse "diff --git a/path b/path"
            try:
                path = line.split(" b/")[-1].strip()
            except IndexError:
                path = "<unknown>"
            current = FileDiff(path=path)
        elif line.startswith("+++ b/") and current:
            current.path = line[6:].strip()
        elif line.startswith("@@"):
            # @@ -old,n +new,n @@
            try:
                segments = line.split("@@")[1].strip().split(" ")
                new_part = next(s for s in segments if s.startswith("+"))
                new_line = int(new_part[1:].split(",")[0]) - 1
                old_part = next(s for s in segments if s.startswith("-"))
                old_line = int(old_part[1:].split(",")[0]) - 1
            except (ValueError, StopIteration, IndexError):
                pass
        elif current and line.startswith("+") and not line.startswith("+++"):
            new_line += 1
            current.added.append((new_line, line[1:]))
            current.new_content += line[1:] + "\n"
        elif current and line.startswith("-") and not line.startswith("---"):
            old_line += 1
            current.removed.append((old_line, line[1:]))
        elif current and not line.startswith("\\"):
            new_line += 1
            old_line += 1
            current.new_content += line[1:] + "\n" if line.startswith(" ") else line + "\n"
    if current:
        files.append(current)
    return files
