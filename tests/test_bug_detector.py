"""Static rules and the Claude bug pass (mocked)."""

import pytest

from src.analyzers.bug_detector import ClaudeBugDetector, build_bug_prompt, static_scan
from src.llm.client import LLMOutputError
from src.parser.diff_parser import FileDiff, parse_unified_diff
from tests.conftest import FakeClaudeClient


def _py(added: list[tuple[int, str]], path: str = "x.py") -> FileDiff:
    return FileDiff(path=path, added=added)


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("    except:", "static:bare-except"),
        ("def f(items=[]):", "static:mutable-default-arg"),
        ("    if x is 'a':", "static:is-literal"),
        ("    if x == None:", "static:eq-none"),
        ("    result = items.sort()", "static:sort-result-assigned"),
        ("    for i in range(len(xs) + 1):", "static:range-len-plus-one"),
        ("    fh = open(path)", "static:open-without-with"),
        ("    assert user.is_admin", "static:assert-in-prod"),
        ("    if type(x) == int:", "static:type-equality"),
        ("    list = [1, 2]", "static:shadow-builtin"),
        ("    print('debug')", "static:print-in-prod"),
        ("    # TODO remove", "static:todo-marker"),
        ("    avg = total / len(items)", "static:division-by-len"),
    ],
)
def test_single_line_rules(line: str, rule: str) -> None:
    comments = static_scan(_py([(1, line)]))
    assert rule in {c.rule_id for c in comments}


def test_comment_lines_are_skipped_except_todo() -> None:
    assert static_scan(_py([(1, "# except: nothing")])) == []


def test_clean_code_has_no_comments() -> None:
    assert static_scan(_py([(1, "    return a + b")])) == []


def test_test_files_skip_assert_and_print() -> None:
    f = _py([(1, "    assert x == 1"), (2, "    print(x)")], path="tests/test_x.py")
    assert static_scan(f) == []


def test_swallowed_exception_multiline() -> None:
    diff = """--- a/m.py
+++ b/m.py
@@ -1,1 +1,5 @@
 def get(d, k):
+    try:
+        return d[k]
+    except KeyError:
+        pass
"""
    comments = static_scan(parse_unified_diff(diff)[0])
    assert any(c.rule_id == "static:swallowed-exception" and c.line == 4 for c in comments)


def test_mutate_while_iterating() -> None:
    diff = """--- a/m.py
+++ b/m.py
@@ -1,1 +1,4 @@
 def clean(items):
+    for item in items:
+        if not item:
+            items.remove(item)
"""
    comments = static_scan(parse_unified_diff(diff)[0])
    assert any(c.rule_id == "static:mutate-while-iterating" and c.line == 4 for c in comments)


def test_js_rules_only_on_js_files() -> None:
    assert any(
        c.rule_id == "static:js-eqeq" for c in static_scan(_py([(1, "if (a == b) {")], "a.js"))
    )
    assert not any(c.rule_id == "static:js-eqeq" for c in static_scan(_py([(1, "if a == b:")])))


async def test_claude_detector_disabled_returns_nothing() -> None:
    detector = ClaudeBugDetector(FakeClaudeClient(enabled=False))
    assert await detector.review(_py([(1, "x = 1")])) == ([], None)


async def test_claude_detector_parses_and_snaps_lines() -> None:
    payload = {
        "comments": [
            {
                "file": "x.py",
                "line": 2,
                "severity": "error",
                "category": "bug",
                "body": "Off by one",
                "suggestion": "range(n)",
                "confidence": 0.9,
            },
            {
                "file": "x.py",
                "line": 50,
                "severity": "info",
                "category": "style",
                "body": "far away",
            },
            {"file": "x.py", "line": "bad", "body": "unparseable"},
            {
                "file": "x.py",
                "line": 3,
                "severity": "critical",
                "category": "bug",
                "body": "bad severity",
            },
        ]
    }
    client = FakeClaudeClient(responses=[payload])
    f = _py([(1, "def f(n):"), (2, "    for i in range(n + 1):"), (3, "        pass")])
    comments, usage = await ClaudeBugDetector(client).review(f)
    assert [c.line for c in comments] == [2]
    assert comments[0].source == "claude" and comments[0].rule_id == "claude:bug"
    assert usage is not None and usage.cost_usd > 0
    assert "<added>" in client.calls[0][1]


async def test_claude_detector_rejects_bad_shape() -> None:
    client = FakeClaudeClient(responses=[{"not_comments": 1}])
    with pytest.raises(LLMOutputError):
        await ClaudeBugDetector(client).review(_py([(1, "x = 1")]))


def test_prompt_includes_context_summary() -> None:
    from src.context.builder import FileContext, Symbol

    ctx = FileContext(
        path="x.py", language="python", touched_symbols=[Symbol("f", "function", 1, 2)]
    )
    prompt = build_bug_prompt(_py([(1, "def f():")]), ctx)
    assert "touched: f" in prompt and "<file>x.py</file>" in prompt
