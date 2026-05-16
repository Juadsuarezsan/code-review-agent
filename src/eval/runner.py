"""Eval: bug-detection recall + false-positive rate on the seed diff set."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.analyzers.orchestrator import review_diff
from src.config import get_settings

DATA = Path(__file__).parent.parent.parent / "data" / "eval" / "diffs.jsonl"


async def run_eval() -> dict:
    s = get_settings()
    cases = [json.loads(l) for l in DATA.read_text(encoding="utf-8").splitlines() if l.strip()]
    tp = fn = fp = 0
    case_results = []
    for c in cases:
        comments = await review_diff(c["diff"], claude_model=s.anthropic_model,
                                       api_key=s.anthropic_api_key, max_comments=10)
        flagged = {x.category for x in comments}
        expected = set(c["expected_categories"])
        matched = expected & flagged
        tp += len(matched)
        fn += len(expected - matched)
        # Only count FP as categories flagged when expected was empty
        if not expected and flagged:
            fp += len(flagged)
        case_results.append({
            "id": c["id"], "expected": list(expected),
            "flagged": list(flagged), "matched": list(matched), "n_comments": len(comments),
        })
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    return {
        "n": len(cases),
        "true_positives": tp, "false_negatives": fn, "false_positives": fp,
        "recall": recall, "precision": precision,
        "f1": 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0,
        "cases": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    r = asyncio.run(run_eval())
    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(f"Cases: {r['n']}  recall={r['recall']:.1%}  precision={r['precision']:.1%}  F1={r['f1']:.2f}")
        print(f"TP={r['true_positives']}  FN={r['false_negatives']}  FP={r['false_positives']}")


if __name__ == "__main__":
    main()
