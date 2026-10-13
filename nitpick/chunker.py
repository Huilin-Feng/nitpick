"""Turn parsed file diffs into LLM-sized chunks.

Strategy:
  1. Skip files that are not worth reviewing (binary, deleted, lock files,
     minified or generated output).
  2. Order the remaining files by priority: source code, then tests, then
     config and docs. If the review budget runs out, the least important
     files are the ones left out.
  3. Split each file into pieces at hunk boundaries, so a large file is
     spread over several chunks instead of being cut off. A single hunk
     that is still too large is split by lines.
  4. Pack pieces into chunks of at most `max_tokens`, and stop after
     `max_chunks` chunks so one huge PR cannot run up an unbounded LLM bill.

Tokens are still estimated as len(text) // 4.
"""

from dataclasses import dataclass
from fnmatch import fnmatch
from posixpath import basename

from nitpick.models import Chunk, DiffLine, FileDiff, SkippedFile

DEFAULT_IGNORE = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "*.min.js",
    "*.min.css",
    "*.map",
    "dist/",
    "build/",
    "node_modules/",
]

PRIORITY_SOURCE = 0
PRIORITY_TEST = 1
PRIORITY_OTHER = 2

OTHER_SUFFIXES = (".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".lock")


def estimate_tokens(text: str) -> int:
    # Rough rule of thumb for English/code: about 4 characters per token.
    return max(1, len(text) // 4)


def is_ignored(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            # Directory pattern: matches that directory at any depth.
            if f"/{pattern}" in f"/{path}":
                return True
        elif fnmatch(basename(path), pattern) or fnmatch(path, pattern):
            return True
    return False


def file_priority(path: str) -> int:
    name = basename(path)
    if (
        path.startswith("tests/")
        or "/tests/" in path
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts"))
    ):
        return PRIORITY_TEST
    if name.endswith(OTHER_SUFFIXES) or name in ("Dockerfile", "Makefile"):
        return PRIORITY_OTHER
    return PRIORITY_SOURCE


def file_header(file: FileDiff, continued: bool = False) -> str:
    suffix = ", continued" if continued else ""
    return f"### File: {file.path} ({file.status}{suffix})"


def render_line(line: DiffLine) -> str:
    # Line numbers are written into the prompt so the LLM can refer to an
    # exact line; otherwise it has to count lines itself and often gets
    # them wrong.
    number = "" if line.new_lineno is None else str(line.new_lineno)
    return f"{number:>5} {line.kind} {line.content}"


def render_file(file: FileDiff) -> str:
    """Render a whole file diff (used when it fits in one chunk)."""
    out = [file_header(file)]
    for hunk in file.hunks:
        out.append(hunk.header)
        out.extend(render_line(line) for line in hunk.lines)
    return "\n".join(out)


@dataclass
class Piece:
    """Part of one file's diff, small enough to fit in a single chunk."""

    file: FileDiff
    text: str
    tokens: int


def split_file(file: FileDiff, max_tokens: int) -> list[Piece]:
    """Split a file diff into pieces that each fit within max_tokens."""
    whole = render_file(file)
    if estimate_tokens(whole) <= max_tokens:
        return [Piece(file, whole, estimate_tokens(whole))]

    pieces: list[Piece] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if current:
            header = file_header(file, continued=bool(pieces))
            text = "\n".join([header, *current])
            pieces.append(Piece(file, text, estimate_tokens(text)))
        current = []

    def fits(extra: list[str]) -> bool:
        header = file_header(file, continued=True)
        return estimate_tokens("\n".join([header, *current, *extra])) <= max_tokens

    for hunk in file.hunks:
        block = [hunk.header, *(render_line(line) for line in hunk.lines)]
        if fits(block):
            current.extend(block)
            continue

        flush()
        if fits(block):
            current.extend(block)
            continue

        # One hunk on its own is too large: split it by lines. Every line
        # keeps its own line number, so the LLM can still point at it.
        part = 1
        current.append(f"{hunk.header} (part {part})")
        lines_in_part = 0
        for line in hunk.lines:
            rendered = render_line(line)
            # A single line longer than the budget is kept anyway rather
            # than producing an empty part.
            if lines_in_part and not fits([rendered]):
                flush()
                part += 1
                current.append(f"{hunk.header} (part {part})")
                lines_in_part = 0
            current.append(rendered)
            lines_in_part += 1

    flush()
    return pieces


def build_chunks(
    files: list[FileDiff],
    max_tokens: int,
    max_chunks: int = 5,
    ignore_patterns: list[str] | None = None,
) -> tuple[list[Chunk], list[SkippedFile]]:
    patterns = DEFAULT_IGNORE if ignore_patterns is None else ignore_patterns
    skipped: list[SkippedFile] = []
    reviewable: list[FileDiff] = []

    for file in files:
        if file.is_binary:
            skipped.append(SkippedFile(file.path, "binary file"))
        elif file.status == "deleted":
            skipped.append(SkippedFile(file.path, "file deleted"))
        elif not file.hunks:
            skipped.append(SkippedFile(file.path, "no textual changes"))
        elif is_ignored(file.path, patterns):
            skipped.append(SkippedFile(file.path, "ignored (generated or lock file)"))
        else:
            reviewable.append(file)

    # sorted() is stable, so files keep their diff order within a priority.
    reviewable.sort(key=lambda f: file_priority(f.path))

    pieces = [piece for file in reviewable for piece in split_file(file, max_tokens)]

    chunks: list[Chunk] = []
    current: list[Piece] = []

    def flush() -> None:
        nonlocal current
        if current:
            files_in_chunk = list(dict.fromkeys(p.file.path for p in current))
            by_path = {p.file.path: p.file for p in current}
            text = "\n\n".join(p.text for p in current)
            chunks.append(Chunk([by_path[p] for p in files_in_chunk], text, estimate_tokens(text)))
        current = []

    for piece in pieces:
        if current and estimate_tokens("\n\n".join([*(p.text for p in current), piece.text])) > max_tokens:
            flush()
        current.append(piece)
    flush()

    if len(chunks) > max_chunks:
        kept, dropped = chunks[:max_chunks], chunks[max_chunks:]
        kept_paths = {f.path for c in kept for f in c.files}
        for path in dict.fromkeys(f.path for c in dropped for f in c.files):
            reason = "partially reviewed: review budget reached" if path in kept_paths else "not reviewed: review budget reached"
            skipped.append(SkippedFile(path, reason))
        chunks = kept

    return chunks, skipped
