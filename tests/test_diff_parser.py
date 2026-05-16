from src.parser.diff_parser import parse_unified_diff


def test_single_file_diff_parsed():
    diff = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,4 @@
 def add(a, b):
-    return a - b
+    return a + b
+
+def sub(a, b):
+    return a - b
"""
    files = parse_unified_diff(diff)
    assert len(files) == 1
    f = files[0]
    assert f.path == "foo.py"
    assert f.n_added == 4
    assert f.n_removed == 1


def test_empty_diff():
    assert parse_unified_diff("") == []


def test_multi_file_diff():
    diff = """diff --git a/a.py b/a.py
+++ b/a.py
@@ -1 +1,2 @@
+import os
diff --git a/b.py b/b.py
+++ b/b.py
@@ -1 +1,2 @@
+import sys
"""
    files = parse_unified_diff(diff)
    assert len(files) == 2
    assert {f.path for f in files} == {"a.py", "b.py"}
