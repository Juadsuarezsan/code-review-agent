"""Test Gap Analyzer tests."""

from src.analyzers.test_gap import detect_test_gaps
from src.context.builder import build_contexts
from src.parser.diff_parser import FileDiff, parse_unified_diff

SRC_DIFF = """--- a/pkg/calc.py
+++ b/pkg/calc.py
@@ -1,4 +1,5 @@
 def total(items):
+    items = [i for i in items if i is not None]
     return sum(items)

 def _hidden():
"""


def test_modified_public_function_without_tests_is_flagged() -> None:
    files = parse_unified_diff(SRC_DIFF)
    comments = detect_test_gaps(files, build_contexts(files))
    assert len(comments) == 1
    c = comments[0]
    assert c.category == "test_gap" and c.file == "pkg/calc.py" and c.line == 2
    assert "adds no tests" in c.body and c.confidence == 0.7
    assert "tests/test_calc.py" in (c.suggestion or "")


def test_test_change_referencing_function_clears_gap() -> None:
    diff = (
        SRC_DIFF
        + "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1,1 +1,2 @@\n import x\n+def test_total(): assert total([1, None]) == 1\n"
    )
    files = parse_unified_diff(diff)
    assert detect_test_gaps(files, build_contexts(files)) == []


def test_unrelated_test_change_lowers_confidence() -> None:
    diff = (
        SRC_DIFF
        + "--- a/tests/test_other.py\n+++ b/tests/test_other.py\n@@ -1,1 +1,2 @@\n import x\n+def test_other(): pass\n"
    )
    files = parse_unified_diff(diff)
    comments = detect_test_gaps(files, build_contexts(files))
    assert len(comments) == 1 and comments[0].confidence == 0.55
    assert "never reference" in comments[0].body


def test_regex_fallback_without_contexts() -> None:
    files = [
        FileDiff(
            path="src/new_module.py", added=[(1, "def new_public(x):"), (2, "    return x * 2")]
        )
    ]
    assert len(detect_test_gaps(files)) == 1
    assert detect_test_gaps([FileDiff(path="src/x.py", added=[(1, "def _internal():")])]) == []


def test_cap_per_file() -> None:
    added = [(i, f"def fn{i}():") for i in range(1, 8)]
    comments = detect_test_gaps([FileDiff(path="src/many.py", added=added)])
    assert len(comments) == 3
