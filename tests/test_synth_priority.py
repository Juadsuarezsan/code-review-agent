"""Comment Synthesizer and Priority Filter tests."""

from src.analyzers.priority import prioritize, score_comment
from src.analyzers.synthesizer import (
    dedupe_comments,
    format_comment_markdown,
    normalize_comment,
    sanitize_text,
    synthesize_deterministic,
    synthesize_with_claude,
)
from src.api.schemas import ReviewComment
from tests.conftest import FakeClaudeClient


def _c(line: int = 1, **kw: object) -> ReviewComment:
    base: dict[str, object] = {
        "file": "x.py",
        "line": line,
        "severity": "warning",
        "category": "bug",
        "body": "Something",
        "rule_id": "static:a",
        "confidence": 0.8,
    }
    base.update(kw)
    return ReviewComment(**base)  # type: ignore[arg-type]


def test_sanitize_strips_tags_and_control_chars() -> None:
    assert sanitize_text("<script>x</script>hi\x00 there") == "xhi there"
    assert sanitize_text(None) is None
    assert sanitize_text("   ") is None


def test_normalize_adds_period_and_justification() -> None:
    c = normalize_comment(_c(body="No period", justification=None))
    assert c.body == "No period." and c.justification is not None and "static:a" in c.justification


def test_dedupe_keeps_strongest_and_merges_rule_ids() -> None:
    a = _c(severity="info", rule_id="ruff:E711", suggestion=None)
    b = _c(severity="warning", rule_id="static:eq-none", suggestion="is None")
    merged = dedupe_comments([a, b])
    assert len(merged) == 1
    assert merged[0].severity == "warning"
    assert merged[0].rule_id == "static:eq-none + ruff:E711"
    assert merged[0].suggestion == "is None"


def test_dedupe_is_per_category() -> None:
    assert len(synthesize_deterministic([_c(category="bug"), _c(category="security")])) == 2


def test_priority_order_and_caps() -> None:
    comments = [
        _c(line=1, severity="info", category="style"),
        _c(line=2, severity="error", category="security"),
        _c(line=3, severity="warning", category="bug"),
        _c(line=4, severity="error", category="bug", file="y.py"),
        _c(line=5, severity="error", category="bug", file="y.py"),
        _c(line=6, severity="error", category="bug", file="y.py"),
    ]
    top = prioritize(comments, max_total=4, max_per_file=2)
    assert [(c.file, c.line) for c in top] == [("x.py", 2), ("x.py", 3), ("y.py", 4), ("y.py", 5)]
    assert score_comment(comments[1]) > score_comment(comments[2]) > score_comment(comments[0])


def test_format_comment_markdown() -> None:
    md = format_comment_markdown(_c(suggestion="x is None", justification="because"))
    assert "WARNING · bug" in md and "x is None" in md and "_Why:_ because" in md


async def test_synthesize_with_claude_rewrites_only_known_comments() -> None:
    original = [_c(line=1, body="Old body"), _c(line=2, body="Keep me")]
    client = FakeClaudeClient(
        responses=[
            {
                "comments": [
                    {
                        "file": "x.py",
                        "line": 1,
                        "severity": "error",
                        "body": "<b>New body</b>",
                        "suggestion": "fix()",
                        "justification": "why",
                    },
                    {"file": "x.py", "line": 99, "severity": "error", "body": "invented"},
                    {"file": "x.py", "line": "nope"},
                    "garbage",
                ]
            }
        ]
    )
    rewritten, usage = await synthesize_with_claude(original, [], client)
    assert [c.body for c in rewritten] == ["New body", "Keep me"]
    assert rewritten[0].severity == "error" and rewritten[0].suggestion == "fix()"
    assert usage is not None and len(rewritten) == 2


async def test_synthesize_with_claude_falls_back_on_bad_output() -> None:
    original = [_c(line=1)]
    client = FakeClaudeClient(responses=["this is not json at all"])
    rewritten, usage = await synthesize_with_claude(original, [], client)
    assert rewritten == original and usage is None
    assert await synthesize_with_claude([], [], client) == ([], None)
