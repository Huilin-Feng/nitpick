"""Turn parsed file diffs into LLM-sized chunks.

==========================================================================
EXTENSION POINT #1 (your design): smarter chunking
--------------------------------------------------------------------------
The current strategy is intentionally naive:
  * files are packed greedily in the order they appear in the diff
  * a single file that is too big is cut off at the hunk boundary,
    and the remaining hunks are simply dropped
  * tokens are estimated as len(text) // 4

Ideas to replace it with your own design:
  * split large files by hunk or by function, instead of dropping hunks
  * prioritise files (source code before lock files / generated files)
  * count tokens exactly with tiktoken
  * respect per-repo ignore patterns (e.g. "*.lock", "migrations/")
==========================================================================
"""

from nitpick.models import Chunk, FileDiff, SkippedFile


def estimate_tokens(text: str) -> int:
    # Rough rule of thumb for English/code: about 4 characters per token.
    return max(1, len(text) // 4)


def render_file(file: FileDiff, max_tokens: int | None = None) -> tuple[str, bool]:
    """Render a file diff with explicit new-file line numbers.

    Line numbers are written into the prompt so the LLM can refer to an
    exact line; otherwise it has to count lines itself and often gets
    them wrong. Returns (text, was_truncated).
    """
    out = [f"### File: {file.path} ({file.status})"]
    truncated = False

    for hunk in file.hunks:
        block = [hunk.header]
        for line in hunk.lines:
            number = "" if line.new_lineno is None else str(line.new_lineno)
            block.append(f"{number:>5} {line.kind} {line.content}")
        candidate = "\n".join(out + block)
        if max_tokens is not None and estimate_tokens(candidate) > max_tokens:
            truncated = True
            break
        out.extend(block)

    return "\n".join(out), truncated


def build_chunks(files: list[FileDiff], max_tokens: int) -> tuple[list[Chunk], list[SkippedFile]]:
    chunks: list[Chunk] = []
    skipped: list[SkippedFile] = []

    current_files: list[FileDiff] = []
    current_parts: list[str] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current_files, current_parts, current_tokens
        if current_files:
            chunks.append(Chunk(current_files, "\n\n".join(current_parts), current_tokens))
        current_files, current_parts, current_tokens = [], [], 0

    for file in files:
        if file.is_binary:
            skipped.append(SkippedFile(file.path, "binary file"))
            continue
        if file.status == "deleted":
            skipped.append(SkippedFile(file.path, "file deleted"))
            continue
        if not file.hunks:
            skipped.append(SkippedFile(file.path, "no textual changes"))
            continue

        text, _ = render_file(file)
        tokens = estimate_tokens(text)

        if tokens > max_tokens:
            # Naive fallback: keep as many leading hunks as fit, drop the rest.
            text, _ = render_file(file, max_tokens)
            if len(text.splitlines()) <= 1:
                # Even the first hunk does not fit: nothing useful to send.
                skipped.append(SkippedFile(file.path, "too large to review"))
                continue
            flush()
            chunks.append(Chunk([file], text, estimate_tokens(text)))
            skipped.append(SkippedFile(file.path, "partially reviewed: too large"))
            continue

        if current_tokens + tokens > max_tokens:
            flush()
        current_files.append(file)
        current_parts.append(text)
        current_tokens += tokens

    flush()
    return chunks, skipped
