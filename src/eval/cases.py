"""Loading of evaluation cases (synthetic set or SWE-bench Lite ground truth)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_CASES = ROOT / "data" / "eval" / "cases.jsonl"


@dataclass(frozen=True)
class GroundTruth:
    """One planted defect: file, new-side line, category."""

    file: str
    line: int
    category: str
    note: str = ""


@dataclass
class EvalCase:
    """One pull request to review, with its expected findings."""

    id: str
    title: str
    diff: str
    ground_truth: list[GroundTruth] = field(default_factory=list)
    synthetic: bool = True
    focus: str = "bug"

    @property
    def clean(self) -> bool:
        """True when the PR has no planted defects (used for the FP rate)."""
        return not self.ground_truth


def load_cases(path: Path = SYNTHETIC_CASES) -> list[EvalCase]:
    """Read a JSONL cases file into :class:`EvalCase` objects."""
    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            raw: dict[str, Any] = json.loads(line)
            cases.append(
                EvalCase(
                    id=str(raw["id"]),
                    title=str(raw.get("title", "")),
                    diff=str(raw["diff"]),
                    ground_truth=[
                        GroundTruth(
                            file=str(g["file"]),
                            line=int(g["line"]),
                            category=str(g["category"]),
                            note=str(g.get("note", "")),
                        )
                        for g in raw.get("ground_truth", [])
                    ],
                    synthetic=bool(raw.get("synthetic", True)),
                    focus=str(raw.get("focus", "bug")),
                )
            )
    return cases
