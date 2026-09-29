"""Unified diff parser tests."""

import pytest

from src.parser.diff_parser import (
    DiffParseError,
    FileDiff,
    parse_unified_diff,
    parse_unified_diff_strict,
)

GIT_DIFF = """diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,5 @@
 def add(a, b):
-    return a - b
+    return a + b
+
+def sub(a, b):
+    return a - b
diff --git a/tests/test_foo.py b/tests/test_foo.py
new file mode 100644
--- /dev/null
+++ b/tests/test_foo.py
@@ -0,0 +1,2 @@
+def test_add():
+    assert add(1, 2) == 3
\\ No newline at end of file
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x = 1
-y = 2
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
"""


def test_git_diff_parses_all_text_files() -> None:
    files = parse_unified_diff(GIT_DIFF)
    assert [f.path for f in files] == ["foo.py", "tests/test_foo.py", "old.py"]


def test_line_numbers_follow_new_side() -> None:
    foo = parse_unified_diff(GIT_DIFF)[0]
    assert foo.added == [
        (2, "    return a + b"),
        (3, ""),
        (4, "def sub(a, b):"),
        (5, "    return a - b"),
    ]
    assert foo.removed == [(2, "    return a - b")]
    assert foo.new_lines[1] == "def add(a, b):"
    assert foo.n_added == 4 and foo.n_removed == 1
    assert foo.added_line_numbers == {2, 3, 4, 5}


def test_new_and_deleted_flags() -> None:
    files = parse_unified_diff(GIT_DIFF)
    assert files[1].is_new_file and not files[1].is_deleted
    assert files[2].is_deleted and files[2].path == "old.py"
    assert files[1].is_test_file and not files[0].is_test_file


def test_new_snippet_pads_invisible_lines() -> None:
    diff = """--- a/x.py
+++ b/x.py
@@ -10,2 +10,3 @@
 def f():
+    return 1

"""
    f = parse_unified_diff(diff)[0]
    snippet = f.new_snippet().splitlines()
    assert len(snippet) == 12
    assert snippet[9] == "def f():" and snippet[10] == "    return 1"
    assert snippet[0] == ""


def test_language_detection() -> None:
    assert FileDiff(path="a/b.py").language == "python"
    assert FileDiff(path="web/app.tsx").language == "typescript"
    assert FileDiff(path="README").language == "unknown"


def test_constructor_with_added_only_populates_new_lines() -> None:
    f = FileDiff(path="x.py", added=[(3, "print(1)")])
    assert f.new_lines == {3: "print(1)"}
    assert f.new_content == "print(1)\n"


def test_empty_and_malformed_input() -> None:
    assert parse_unified_diff("") == []
    assert parse_unified_diff("just some prose\nwith no hunks") == []
    with pytest.raises(DiffParseError):
        parse_unified_diff_strict("   ")
    with pytest.raises(DiffParseError):
        parse_unified_diff_strict("hello world")


def test_hunk_header_without_lengths() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
    f = parse_unified_diff(diff)[0]
    assert f.hunks[0].new_start == 1 and f.hunks[0].new_len == 1
    assert f.added == [(1, "a = 2")]
