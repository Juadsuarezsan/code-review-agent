"""Style Checker tests: real ruff subprocess plus failure modes with mocks."""

import subprocess

from pytest_mock import MockerFixture

from src.analyzers.style_checker import (
    LintFinding,
    RuffLinter,
    StyleCheckError,
    check_style,
    parse_ruff_json,
)
from src.parser.diff_parser import parse_unified_diff

DIFF = """--- a/m.py
+++ b/m.py
@@ -1,3 +1,5 @@
 def check(x, y):
+    if x == None:
+        return y
     if y == None:
         return x
"""


def test_ruff_reports_only_added_lines() -> None:
    f = parse_unified_diff(DIFF)[0]
    comments = check_style(f, RuffLinter())
    assert [c.line for c in comments] == [2]
    assert comments[0].rule_id == "ruff:E711" and comments[0].category == "style"
    assert comments[0].source == "ruff"


def test_ruff_bug_codes_map_to_bug_category() -> None:
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,2 @@\n def f(x):\n+    return x is 'a'\n"
    comments = check_style(parse_unified_diff(diff)[0])
    assert any(
        c.rule_id == "ruff:F632" and c.category == "bug" and c.severity == "error" for c in comments
    )


def test_non_python_files_are_skipped() -> None:
    diff = "--- a/a.js\n+++ b/a.js\n@@ -1,1 +1,2 @@\n var a;\n+var b = a == null;\n"
    assert check_style(parse_unified_diff(diff)[0]) == []


def test_parse_ruff_json_handles_empty_and_fix() -> None:
    assert parse_ruff_json("") == []
    payload = (
        '[{"code": "E711", "message": "m", "location": {"row": 4}, "fix": {"message": "use is"}}]'
    )
    assert parse_ruff_json(payload) == [LintFinding(line=4, code="E711", message="m", fix="use is")]


def test_linter_process_failure_is_logged_not_raised(mocker: MockerFixture) -> None:
    mocker.patch(
        "src.analyzers.style_checker.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=2, stdout="", stderr="boom"),
    )
    assert check_style(parse_unified_diff(DIFF)[0], RuffLinter()) == []


def test_linter_timeout_raises_style_check_error(mocker: MockerFixture) -> None:
    mocker.patch(
        "src.analyzers.style_checker.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ruff", timeout=1),
    )
    try:
        RuffLinter(timeout_seconds=1).lint("m.py", "x = 1\n")
    except StyleCheckError as exc:
        assert "timed out" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected StyleCheckError")


def test_invalid_json_from_linter(mocker: MockerFixture) -> None:
    mocker.patch(
        "src.analyzers.style_checker.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=1, stdout="{not json", stderr=""
        ),
    )
    assert check_style(parse_unified_diff(DIFF)[0], RuffLinter()) == []
