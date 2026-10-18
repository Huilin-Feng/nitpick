"""Parse unified diffs and pull out the symbols a PR touches."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
_DEF_RE = re.compile(
    r"^\s*(?:async\s+def|def|class|function|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
# JS/TS style: const foo = (...) =>  /  export const foo = function
_ARROW_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(|function)")

# Names too generic to be useful as search terms
STOP_SYMBOLS = {
    "__init__", "__repr__", "__str__", "__eq__", "__call__", "main", "run", "get", "set",
    "init", "setup", "test", "self", "cls", "data", "value", "update", "create", "delete",
}
MIN_SYMBOL_LEN = 4


@dataclass
class Hunk:
    old_start: int
    new_start: int
    header_context: str            # text after the second @@, often the enclosing def
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # every line in order as (kind, text), kind in "+", "-", " "; keeps the
    # unchanged context so we can tell which function a change sits in
    lines: list[tuple[str, str]] = field(default_factory=list)

    def query_text(self) -> str:
        """Text used as the semantic search query for this hunk."""
        parts = []
        if self.header_context:
            parts.append(self.header_context)
        parts.extend(self.removed)
        parts.extend(self.added)
        return "\n".join(parts).strip()


@dataclass
class FileDiff:
    old_path: str | None   # None for newly added files
    new_path: str | None   # None for deleted files
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _strip_prefix(p: str) -> str | None:
    p = p.strip().split("\t")[0]
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Parse a unified diff (as produced by git / the GitHub API).

    Hunk line counts from the @@ header are tracked, so content lines that happen
    to start with "---" or "+++" are never mistaken for file headers.
    """
    lines = diff_text.splitlines()
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_left = new_left = 0  # lines still expected in the current hunk
    i = 0
    while i < len(lines):
        line = lines[i]
        if hunk is not None and (old_left > 0 or new_left > 0):
            if line.startswith("+"):
                hunk.added.append(line[1:])
                hunk.lines.append(("+", line[1:]))
                new_left -= 1
            elif line.startswith("-"):
                hunk.removed.append(line[1:])
                hunk.lines.append(("-", line[1:]))
                old_left -= 1
            elif line.startswith("\\"):
                pass  # "\ No newline at end of file"
            else:  # context line (" x" or an empty line whose space was stripped)
                hunk.lines.append((" ", line[1:]))
                old_left -= 1
                new_left -= 1
            i += 1
            continue
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            current = FileDiff(old_path=_strip_prefix(line[4:]), new_path=_strip_prefix(lines[i + 1][4:]))
            files.append(current)
            hunk = None
            i += 2
            continue
        if line.startswith("diff --git "):
            current, hunk = None, None
            i += 1
            continue
        m = _HUNK_RE.match(line)
        if m and current is not None:
            hunk = Hunk(old_start=int(m.group(1)), new_start=int(m.group(3)),
                        header_context=m.group(5).strip())
            current.hunks.append(hunk)
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
        i += 1
    return files


def changed_paths(files: list[FileDiff]) -> set[str]:
    paths: set[str] = set()
    for f in files:
        for p in (f.old_path, f.new_path):
            if p:
                paths.add(p)
    return paths


def _symbol_from_line(line: str) -> str | None:
    for regex in (_DEF_RE, _ARROW_RE):
        m = regex.match(line)
        if m:
            return m.group(1)
    return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _usable(name: str | None) -> bool:
    return bool(name) and len(name) >= MIN_SYMBOL_LEN and name not in STOP_SYMBOLS


def extract_changed_symbols(files: list[FileDiff]) -> list[str]:
    """Functions/classes the PR changes. Their callers elsewhere may need updating.

    Three sources:
      * def/class lines that were added or removed (signature changes, renames)
      * the enclosing def from the @@ header (git adds it when the def is above the hunk)
      * the enclosing def found in the hunk's own context lines, for a body-only
        change whose def line is shown unchanged (the header is then empty,
        e.g. for a short file where the hunk starts at line 1)

    Order is stable (first seen first) so retrieval is deterministic.
    """
    seen: dict[str, None] = {}

    def add(name: str | None) -> None:
        if _usable(name):
            seen.setdefault(name, None)

    for f in files:
        for h in f.hunks:
            if h.header_context:
                add(_symbol_from_line(h.header_context))
            enclosing: tuple[str, int] | None = None  # (name, indent) of the last def seen
            for kind, text in h.lines:
                name = _symbol_from_line(text)
                if name:
                    if kind != " ":
                        add(name)
                    enclosing = (name, _indent(text))
                    continue
                if kind != " " and text.strip() and enclosing and _indent(text) > enclosing[1]:
                    add(enclosing[0])
    return list(seen)


# A bare call `name(` (not `obj.name(`), and names pulled in by `from x import a, b`
_CALL_RE = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_FROM_IMPORT_RE = re.compile(r"^\s*from\s+[\w.]+\s+import\s+\(?([\w\s,]+)\)?")
_NOT_CALLEES = {
    # Python keywords and builtins that look like calls
    "if", "elif", "while", "for", "return", "yield", "assert", "print", "len", "range", "str",
    "int", "float", "bool", "dict", "list", "set", "tuple", "sorted", "reversed", "isinstance",
    "issubclass", "super", "open", "enumerate", "zip", "map", "filter", "sum", "min", "max",
    "abs", "round", "repr", "type", "getattr", "setattr", "hasattr", "iter", "next", "any", "all",
    "Exception", "ValueError", "TypeError", "KeyError", "RuntimeError", "NotImplementedError",
}


def extract_used_names(files: list[FileDiff]) -> list[str]:
    """Functions the added code calls or imports. Their definitions may matter.

    Example: a new helper sorts the list returned by `load_items()`; whether
    that is a bug depends on how `load_items` is written, which is outside the diff.
    Names defined in the diff itself are skipped (their code is already visible).
    """
    defined = {_symbol_from_line(l) for f in files for h in f.hunks for _, l in h.lines}
    seen: dict[str, None] = {}
    for f in files:
        for h in f.hunks:
            for line in h.added:
                names = _CALL_RE.findall(line)
                m = _FROM_IMPORT_RE.match(line)
                if m:
                    names += [n.strip().split(" as ")[0] for n in m.group(1).split(",")]
                for name in names:
                    if name not in _NOT_CALLEES and name not in defined and _usable(name):
                        seen.setdefault(name, None)
    return list(seen)
