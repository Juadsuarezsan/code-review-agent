"""Context Builder: derive functions, classes and imports around the changed lines.

Two interchangeable parsers implement :class:`AstParser`:

* :class:`TreeSitterPythonParser` (default) uses ``tree-sitter`` with the
  ``tree-sitter-python`` grammar. It is error-tolerant, which matters because a
  diff only shows fragments of a file.
* :class:`StdlibAstParser` uses the standard library ``ast`` module and is the
  fallback when the tree-sitter wheels are unavailable. It requires syntactically
  valid input, so it degrades to "no symbols" on partial snippets.

Only Python is analyzed structurally today; other languages get a
:class:`FileContext` with ``parse_backend="none"``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger

from src.parser.diff_parser import FileDiff


@dataclass(frozen=True)
class Symbol:
    """A function, method or class definition with its line span (1-based, inclusive)."""

    name: str
    kind: str  # "function" | "method" | "class"
    start_line: int
    end_line: int
    parent: str | None = None

    @property
    def qualified_name(self) -> str:
        """``Class.method`` for methods, plain name otherwise."""
        return f"{self.parent}.{self.name}" if self.parent else self.name

    def contains(self, line: int) -> bool:
        """True when ``line`` falls inside this symbol's span."""
        return self.start_line <= line <= self.end_line


@dataclass
class FileContext:
    """Structural context for one changed file."""

    path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    touched_symbols: list[Symbol] = field(default_factory=list)
    related_paths: list[str] = field(default_factory=list)
    parse_backend: str = "none"
    parse_error: str | None = None

    def enclosing_symbol(self, line: int) -> Symbol | None:
        """Innermost symbol containing ``line`` (methods win over their class)."""
        candidates = [s for s in self.symbols if s.contains(line)]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.end_line - s.start_line)

    def summary(self) -> str:
        """Compact text used in LLM prompts."""
        touched = ", ".join(s.qualified_name for s in self.touched_symbols) or "(module level)"
        imports = ", ".join(self.imports[:15]) or "(none visible)"
        return f"touched: {touched}\nimports: {imports}"


class ParseError(ValueError):
    """Raised by :class:`StdlibAstParser` on syntactically invalid input."""


class AstParser(Protocol):
    """Extract symbols and imports from Python source."""

    name: str

    def parse(self, source: str) -> tuple[list[Symbol], list[str]]:
        """Return ``(symbols, import_statements)`` for ``source``."""
        ...


class TreeSitterPythonParser:
    """Error-tolerant Python parser backed by ``tree-sitter-python``."""

    name = "tree-sitter"

    def __init__(self) -> None:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        self._parser = Parser(Language(tspython.language()))

    def parse(self, source: str) -> tuple[list[Symbol], list[str]]:
        """Walk the concrete syntax tree collecting definitions and imports."""
        tree = self._parser.parse(source.encode("utf-8"))
        symbols: list[Symbol] = []
        imports: list[str] = []

        def text(node: object) -> str:
            raw = getattr(node, "text", None)
            return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else ""

        def walk(node: object, parent_class: str | None) -> None:
            node_type = getattr(node, "type", "")
            if node_type in ("function_definition", "class_definition"):
                name_node = node.child_by_field_name("name")  # type: ignore[attr-defined]
                name = text(name_node) if name_node is not None else "<anonymous>"
                start = node.start_point[0] + 1  # type: ignore[attr-defined]
                end = node.end_point[0] + 1  # type: ignore[attr-defined]
                if node_type == "class_definition":
                    symbols.append(Symbol(name, "class", start, end, parent_class))
                    for child in node.children:  # type: ignore[attr-defined]
                        walk(child, name)
                    return
                kind = "method" if parent_class else "function"
                symbols.append(Symbol(name, kind, start, end, parent_class))
                for child in node.children:  # type: ignore[attr-defined]
                    walk(child, None)
                return
            if node_type in ("import_statement", "import_from_statement"):
                imports.append(" ".join(text(node).split()))
                return
            for child in getattr(node, "children", []):
                walk(child, parent_class)

        walk(tree.root_node, None)
        return symbols, imports


class StdlibAstParser:
    """Fallback parser using the standard library; requires valid syntax."""

    name = "ast"

    def parse(self, source: str) -> tuple[list[Symbol], list[str]]:
        """Return definitions and imports, raising :class:`ParseError` on invalid code."""
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise ParseError(str(exc)) from exc
        symbols: list[Symbol] = []
        imports: list[str] = []

        def visit(node: ast.AST, parent_class: str | None) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.ClassDef):
                    symbols.append(
                        Symbol(
                            child.name,
                            "class",
                            child.lineno,
                            child.end_lineno or child.lineno,
                            parent_class,
                        )
                    )
                    visit(child, child.name)
                elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    kind = "method" if parent_class else "function"
                    symbols.append(
                        Symbol(
                            child.name,
                            kind,
                            child.lineno,
                            child.end_lineno or child.lineno,
                            parent_class,
                        )
                    )
                    visit(child, None)
                elif isinstance(child, ast.Import | ast.ImportFrom):
                    imports.append(ast.unparse(child))
                else:
                    visit(child, parent_class)

        visit(tree, None)
        return symbols, imports


def get_parser(prefer: str = "tree-sitter") -> AstParser:
    """Return the preferred parser, falling back to ``ast`` when tree-sitter is missing."""
    if prefer == "tree-sitter":
        try:
            return TreeSitterPythonParser()
        except ImportError as exc:  # pragma: no cover - depends on the environment
            logger.warning("tree-sitter unavailable ({}); falling back to stdlib ast", exc)
    return StdlibAstParser()


def _related_paths(imports: list[str], known_paths: set[str]) -> list[str]:
    """Map ``from a.b import c`` statements to sibling files present in the diff."""
    related: list[str] = []
    for stmt in imports:
        tokens = stmt.replace(",", " ").split()
        if not tokens:
            continue
        module = tokens[1] if tokens[0] in ("from", "import") and len(tokens) > 1 else None
        if not module:
            continue
        candidate = module.replace(".", "/") + ".py"
        for path in known_paths:
            if path == candidate or path.endswith("/" + candidate):
                related.append(path)
    return sorted(set(related))


def build_context(
    file_diff: FileDiff, parser: AstParser | None = None, known_paths: set[str] | None = None
) -> FileContext:
    """Build the :class:`FileContext` for one file.

    Non-Python files return an empty context. Parse failures are recorded in
    ``parse_error`` and never raised, so the pipeline keeps going.
    """
    ctx = FileContext(path=file_diff.path, language=file_diff.language)
    if file_diff.language != "python" or file_diff.is_deleted or not file_diff.new_lines:
        return ctx
    parser = parser or get_parser()
    snippet = file_diff.new_snippet()
    try:
        symbols, imports = parser.parse(snippet)
    except ParseError as exc:
        ctx.parse_backend = parser.name
        ctx.parse_error = str(exc)
        logger.debug("context parse failed path={} backend={} err={}", ctx.path, parser.name, exc)
        return ctx
    ctx.parse_backend = parser.name
    ctx.symbols = symbols
    ctx.imports = imports
    seen: set[str] = set()
    for line_no in sorted(file_diff.added_line_numbers):
        sym = ctx.enclosing_symbol(line_no)
        if sym and sym.qualified_name not in seen:
            seen.add(sym.qualified_name)
            ctx.touched_symbols.append(sym)
    ctx.related_paths = _related_paths(imports, (known_paths or set()) - {file_diff.path})
    return ctx


def build_contexts(files: list[FileDiff], parser: AstParser | None = None) -> list[FileContext]:
    """Build contexts for every file, sharing one parser instance."""
    parser = parser or get_parser()
    known = {f.path for f in files}
    return [build_context(f, parser, known) for f in files]
