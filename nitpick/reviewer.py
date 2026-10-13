"""Core review pipeline: diff text in, ReviewResult out.

This module knows nothing about HTTP or GitHub, which keeps it easy to
test and reuse (CLI, webhook server, or an evaluation script).
"""

import logging

from nitpick.chunker import build_chunks
from nitpick.diff_parser import parse_diff
from nitpick.llm import LLMClient
from nitpick.models import SEVERITY_PRIORITY, ReviewComment, ReviewResult, Severity
from nitpick.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class Reviewer:
    def __init__(
        self,
        llm: LLMClient,
        max_tokens_per_chunk: int = 6000,
        max_comments: int = 10,
        max_chunks: int = 5,
    ) -> None:
        self.llm = llm
        self.max_tokens_per_chunk = max_tokens_per_chunk
        self.max_comments = max_comments
        self.max_chunks = max_chunks

    def review(self, diff_text: str, position_diff_text: str | None = None) -> ReviewResult:
        """Review `diff_text`.

        `position_diff_text` is used for incremental reviews: `diff_text`
        holds only the new commits, but GitHub positions inline comments
        against the whole PR diff. A line can be new in this push yet not
        part of the PR diff (e.g. a change that was reverted back to the
        base version); such comments become notes instead of being rejected.
        """
        files = parse_diff(diff_text)
        chunks, skipped = build_chunks(files, self.max_tokens_per_chunk, self.max_chunks)
        commentable = {f.path: f.commentable_lines() for f in files}
        added = {f.path: f.added_lines() for f in files}
        positionable = None
        if position_diff_text is not None:
            positionable = {f.path: f.commentable_lines() for f in parse_diff(position_diff_text)}

        summaries: list[str] = []
        comments: list[ReviewComment] = []
        notes: list[ReviewComment] = []
        dropped = 0
        failed = 0

        for chunk in chunks:
            try:
                data = self.llm.complete_json(SYSTEM_PROMPT, build_user_prompt(chunk.text))
            except Exception:
                # One failing chunk should not kill the whole review.
                logger.exception("LLM call failed for chunk with %d file(s)", len(chunk.files))
                failed += 1
                continue

            summary = data.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())

            for raw in data.get("comments") or []:
                comment = _to_comment(raw)
                if comment is None:
                    dropped += 1
                elif comment.line in added.get(comment.path, set()) and (
                    positionable is None or comment.line in positionable.get(comment.path, set())
                ):
                    comments.append(comment)
                elif comment.line in commentable.get(comment.path, set()):
                    # Unchanged context line: keep it, but out of the inline review.
                    notes.append(comment)
                else:
                    # Not part of the diff at all; GitHub would reject it.
                    dropped += 1

        comments, cut = _rank_and_cap(comments, self.max_comments)
        notes, cut_notes = _rank_and_cap(notes, self.max_comments)
        dropped += cut + cut_notes

        return ReviewResult(
            summary=" ".join(summaries) or "No significant issues found.",
            comments=comments,
            notes=notes,
            skipped_files=skipped,
            dropped_comments=dropped,
            failed_chunks=failed,
        )


def _rank_and_cap(items: list[ReviewComment], limit: int) -> tuple[list[ReviewComment], int]:
    """Sort by importance and keep at most `limit`; return (kept, number cut)."""
    items = sorted(items, key=lambda c: (SEVERITY_PRIORITY[c.severity], c.path, c.line))
    return items[:limit], max(0, len(items) - limit)


def _to_comment(raw: object) -> ReviewComment | None:
    """Validate one comment from the LLM; return None if it is unusable."""
    if not isinstance(raw, dict):
        return None
    path, line, severity, message = raw.get("path"), raw.get("line"), raw.get("severity"), raw.get("message")

    if not isinstance(path, str) or not isinstance(message, str) or not message.strip():
        return None
    # bool is a subclass of int in Python, so exclude it explicitly.
    if isinstance(line, bool):
        return None
    if isinstance(line, str) and line.isdigit():
        line = int(line)
    if not isinstance(line, int):
        return None
    try:
        sev = Severity(str(severity).lower())
    except ValueError:
        return None

    return ReviewComment(path=path, line=line, severity=sev, message=message.strip())
