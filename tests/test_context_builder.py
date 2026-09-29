"""Context Builder tests (tree-sitter and stdlib ast backends)."""

import pytest

from src.context.builder import (
    ParseError,
    StdlibAstParser,
    TreeSitterPythonParser,
    build_context,
    build_contexts,
    get_parser,
)
from src.parser.diff_parser import parse_unified_diff

SOURCE = """import os
from app.utils import helper

class Repo:
    def save(self, item):
        return helper(item)

    def _private(self):
        pass

async def fetch(url):
    return os.getenv(url)
"""


@pytest.mark.parametrize("parser_cls", [TreeSitterPythonParser, StdlibAstParser])
def test_symbols_and_imports(parser_cls: type) -> None:
    symbols, imports = parser_cls().parse(SOURCE)
    names = {(s.qualified_name, s.kind) for s in symbols}
    assert ("Repo", "class") in names
    assert ("Repo.save", "method") in names
    assert ("fetch", "function") in names
    assert any("os" in i for i in imports) and any("app.utils" in i for i in imports)
    save = next(s for s in symbols if s.name == "save")
    assert save.start_line == 5 and save.end_line == 6 and save.parent == "Repo"


def test_tree_sitter_tolerates_partial_snippet() -> None:
    partial = "\n\n    def broken(self):\n        x = compute(\n"
    symbols, _ = TreeSitterPythonParser().parse(partial)
    assert any(s.name == "broken" for s in symbols)


def test_stdlib_parser_raises_on_invalid_syntax() -> None:
    with pytest.raises(ParseError):
        StdlibAstParser().parse("def broken(:\n")


def test_get_parser_fallback_name() -> None:
    assert get_parser("ast").name == "ast"
    assert get_parser().name == "tree-sitter"


def test_build_context_touched_symbols_and_related_paths() -> None:
    diff = """--- a/app/service.py
+++ b/app/service.py
@@ -1,6 +1,7 @@
 from app.utils import helper

 class Repo:
     def save(self, item):
+        item = helper(item)
         return item

--- a/app/utils.py
+++ b/app/utils.py
@@ -1,2 +1,3 @@
 def helper(x):
+    x = x.strip()
     return x
"""
    files = parse_unified_diff(diff)
    contexts = build_contexts(files)
    svc = contexts[0]
    assert svc.parse_backend == "tree-sitter"
    assert [s.qualified_name for s in svc.touched_symbols] == ["Repo.save"]
    assert svc.related_paths == ["app/utils.py"]
    assert "touched: Repo.save" in svc.summary()
    assert svc.enclosing_symbol(999) is None


def test_build_context_records_parse_error_with_ast_backend() -> None:
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n def f(:\n+    pass\n"
    ctx = build_context(parse_unified_diff(diff)[0], parser=StdlibAstParser())
    assert ctx.parse_error is not None and ctx.symbols == []


def test_non_python_file_gets_empty_context() -> None:
    diff = "--- a/app.js\n+++ b/app.js\n@@ -1,1 +1,2 @@\n var a;\n+var b;\n"
    ctx = build_context(parse_unified_diff(diff)[0])
    assert ctx.parse_backend == "none" and ctx.language == "javascript"
