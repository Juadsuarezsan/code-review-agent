"""LLM-as-judge for comment quality with an explicit, numbered rubric.

The rubric lives in code so that the evaluation is reproducible. Each comment is
scored on three criteria, 1-5 each; ``comment_quality`` is the mean of the three
normalised to 0-1. Running the judge requires ``ANTHROPIC_API_KEY``; without it
the metric is reported as pending.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.api.schemas import ReviewComment
from src.llm.client import ClaudeClient, LLMOutputError, LLMResult

RUBRIC = """Score the code-review comment on three criteria, integers 1-5 each.

1. ACTIONABILITY — can the author act on it without asking questions?
   1 = vague ("this could be improved"), 3 = names the problem but not the fix,
   5 = states exactly what to change, with a concrete suggestion.
   Example 5: "Replace `os.system(cmd)` with `subprocess.run(["tar", ...], check=True)`;
   the shell interpolation lets a crafted filename run commands."
   Example 1: "Potential issue here."

2. SPECIFICITY — does it reference the exact code (variable/function/line) and the
   concrete consequence?
   1 = generic advice that would apply to any file, 3 = mentions the construct,
   5 = names the identifier and the failure mode.
   Example 5: "`registry=[]` is shared across calls, so the second `register()` sees
   the first call's names."
   Example 1: "Be careful with defaults."

3. CORRECTNESS — is the claim true for THIS diff?
   1 = wrong or hallucinated, 3 = plausible but overstated, 5 = accurate and
   the severity matches the impact.
   Example 5: flagging `except:` in a loader that must surface missing files.
   Example 1: claiming an `IndexError` on a loop that stops one before the end.

Return ONLY JSON: {"actionability": int, "specificity": int, "correctness": int,
"rationale": "one sentence"}"""

CRITERIA = ("actionability", "specificity", "correctness")


@dataclass(frozen=True)
class JudgeScore:
    """Scores for one comment."""

    actionability: int
    specificity: int
    correctness: int
    rationale: str

    @property
    def quality(self) -> float:
        """Mean of the three criteria normalised to 0..1."""
        return ((self.actionability + self.specificity + self.correctness) / 3 - 1) / 4


def build_judge_prompt(comment: ReviewComment, diff: str) -> str:
    """User prompt for one comment."""
    payload = {
        "file": comment.file,
        "line": comment.line,
        "severity": comment.severity,
        "category": comment.category,
        "body": comment.body,
        "suggestion": comment.suggestion,
        "justification": comment.justification,
    }
    return f"<diff>\n{diff[:12000]}\n</diff>\n<comment>\n{json.dumps(payload)}\n</comment>"


def parse_judge_output(payload: dict[str, Any]) -> JudgeScore:
    """Validate the judge's JSON; every criterion must be an int in 1..5."""
    scores: dict[str, int] = {}
    for key in CRITERIA:
        try:
            value = int(payload[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMOutputError(f"judge output missing/invalid {key!r}") from exc
        if not 1 <= value <= 5:
            raise LLMOutputError(f"judge score {key}={value} out of range")
        scores[key] = value
    return JudgeScore(
        actionability=scores["actionability"],
        specificity=scores["specificity"],
        correctness=scores["correctness"],
        rationale=str(payload.get("rationale", "")),
    )


async def judge_comment(
    client: ClaudeClient, comment: ReviewComment, diff: str
) -> tuple[JudgeScore, LLMResult]:
    """Score one comment with Claude using :data:`RUBRIC`."""
    payload, usage = await client.complete_json(RUBRIC, build_judge_prompt(comment, diff))
    return parse_judge_output(payload), usage
