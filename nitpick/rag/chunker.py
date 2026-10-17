"""Split source files into chunks.

Python files are split along functions and classes using `ast`, so each chunk is
a meaningful unit. Other files (or Python that fails to parse) fall back to
overlapping line windows.
"""
from __future__ import annotations

import ast

from .models import Chunk

DEFAULT_MAX_LINES = 80      # a class longer than this is split into its methods
WINDOW_LINES = 60
WINDOW_OVERLAP = 10


def chunk_file(path: str, source: str, max_lines: int = DEFAULT_MAX_LINES) -> list[Chunk]:
    path = path.replace("\\", "/")
    if not source.strip():
        return []
    if path.endswith(".py"):
        try:
            return _chunk_python(path, source, max_lines)
        except SyntaxError:
            pass
    return chunk_by_lines(path, source)


def chunk_by_lines(
    path: str,
    source: str,
    window: int = WINDOW_LINES,
    overlap: int = WINDOW_OVERLAP,
    symbol: str | None = None,
    line_offset: int = 0,
) -> list[Chunk]:
    if overlap >= window:
        raise ValueError("overlap must be smaller than window")
    lines = source.splitlines()
    chunks: list[Chunk] = []
    step = window - overlap
    start = 0
    while start < len(lines):
        end = min(start + window, len(lines))
        text = "\n".join(lines[start:end])
        if text.strip():
            chunks.append(Chunk(path, start + 1 + line_offset, end + line_offset, symbol, text))
        if end == len(lines):
            break
        start += step
    return chunks


def _chunk_python(path: str, source: str, max_lines: int) -> list[Chunk]:
    tree = ast.parse(source)
    lines = source.splitlines()
    chunks: list[Chunk] = []
    covered: set[int] = set()  # 1-based line numbers already inside a chunk

    def span(node: ast.AST) -> tuple[int, int]:
        # include decorators in the chunk
        decorators = getattr(node, "decorator_list", [])
        start = min([node.lineno] + [d.lineno for d in decorators])
        return start, node.end_lineno

    def add(start: int, end: int, symbol: str) -> None:
        text = "\n".join(lines[start - 1:end])
        if text.strip():
            chunks.append(Chunk(path, start, end, symbol, text))
            covered.update(range(start, end + 1))

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(*span(node), node.name)
        elif isinstance(node, ast.ClassDef):
            start, end = span(node)
            if end - start + 1 <= max_lines:
                add(start, end, node.name)
                continue
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if not methods:
                for c in chunk_by_lines(path, "\n".join(lines[start - 1:end]), symbol=node.name,
                                        line_offset=start - 1):
                    chunks.append(c)
                    covered.update(range(c.start_line, c.end_line + 1))
                continue
            # class header: class line, docstring, class attributes before first method
            first_method_start = span(methods[0])[0]
            if first_method_start > start:
                add(start, first_method_start - 1, node.name)
            for m in methods:
                m_start, m_end = span(m)
                if m_end - m_start + 1 > max_lines * 2:
                    for c in chunk_by_lines(path, "\n".join(lines[m_start - 1:m_end]),
                                            symbol=f"{node.name}.{m.name}", line_offset=m_start - 1):
                        chunks.append(c)
                        covered.update(range(c.start_line, c.end_line + 1))
                else:
                    add(m_start, m_end, f"{node.name}.{m.name}")

    # module-level code not covered above (imports, constants, scripts)
    _add_uncovered_module_code(path, lines, covered, chunks)
    chunks.sort(key=lambda c: c.start_line)
    return chunks


def _add_uncovered_module_code(path: str, lines: list[str], covered: set[int], chunks: list[Chunk]) -> None:
    run: list[int] = []

    def flush() -> None:
        if not run:
            return
        text = "\n".join(lines[run[0] - 1:run[-1]])
        if text.strip():
            for c in chunk_by_lines(path, text, symbol="<module>", line_offset=run[0] - 1):
                chunks.append(c)
        run.clear()

    for lineno in range(1, len(lines) + 1):
        if lineno in covered:
            flush()
        else:
            run.append(lineno)
    flush()
