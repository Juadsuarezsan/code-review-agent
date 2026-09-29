#!/usr/bin/env python
"""Produce ``demo/predictions.json``: pre-baked reviews for the static demo.

The cases come from the synthetic eval set and are reviewed by the real pipeline
in **linter-only mode** (no API key, deterministic). The output records that mode
explicitly so the demo never presents these as LLM output.

Run: ``python scripts/build_demo_predictions.py``
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.eval.cases import SYNTHETIC_CASES, load_cases  # noqa: E402
from src.graph.pipeline import ReviewPipeline  # noqa: E402
from src.observability import configure_logging  # noqa: E402

DEMO_CASE_IDS = ["syn-001", "syn-014", "syn-017", "syn-023", "syn-024", "syn-031", "syn-035"]
OUT = ROOT / "demo" / "predictions.json"


async def build() -> dict[str, object]:
    """Review the selected cases and assemble the demo payload."""
    settings = Settings(_env_file=None)
    pipeline = ReviewPipeline(settings, use_llm=False)
    cases = {c.id: c for c in load_cases(SYNTHETIC_CASES)}
    items = []
    for case_id in DEMO_CASE_IDS:
        case = cases[case_id]
        result = await pipeline.run(case.diff, max_comments=10)
        items.append(
            {
                "id": case.id,
                "title": case.title,
                "synthetic": True,
                "diff": case.diff,
                "ground_truth": [
                    {"file": g.file, "line": g.line, "category": g.category, "note": g.note}
                    for g in case.ground_truth
                ],
                "review": {
                    "mode": result.mode,
                    "summary": result.summary,
                    "comments": [c.model_dump() for c in result.comments],
                    "latency_ms": result.metrics.latency_ms,
                    "cost_usd": result.metrics.cost_usd,
                    "llm_calls": result.metrics.llm_calls,
                    "node_timings": [vars(t) for t in result.metrics.node_timings],
                },
            }
        )
    return {
        "project": "code-review-agent",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "linter-only",
        "disclaimer": (
            "Pre-baked reviews produced by the repository pipeline in linter-only mode "
            "(deterministic fallback, no LLM). Cases are synthetic PRs with planted defects."
        ),
        "cases": items,
    }


def main() -> int:
    """Write the JSON file."""
    configure_logging("WARNING")
    payload = asyncio.run(build())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {len(payload['cases'])} cases to {OUT}\n")  # type: ignore[arg-type]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
