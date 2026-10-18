"""Core review pipeline: diff text in, ReviewResult out.

This module knows nothing about HTTP or GitHub, which keeps it easy to
test and reuse (CLI, webhook server, or an evaluation script).
"""

import logging
from collections.abc import Callable

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

    def review(
        self,
        diff_text: str,
        position_diff_text: str | None = None,
        related_code: str = "",
        reanchor: Callable[[list], list] | None = None,
    ) -> ReviewResult:
        """Review `diff_text`.

        `position_diff_text` is used for incremental reviews: `diff_text`
        holds only the new commits, but GitHub positions inline comments
        against the whole PR diff. A line can be new in this push yet not
        part of the PR diff (e.g. a change that was reverted back to the
        base version); such comments become notes instead of being rejected.

        `related_code` is repository code retrieved by RAG (rag mode). It is
        appended to every chunk's prompt; pipeline mode passes "".
        `reanchor` (rag mode) may move comments the model put on retrieved code
        onto the changed line that causes them, before placement.
        """
        files = parse_diff(diff_text)
        chunks, skipped = build_chunks(files, self.max_tokens_per_chunk, self.max_chunks)
        placer = CommentPlacer(files, position_diff_text)

        summaries: list[str] = []
        comments: list[ReviewComment] = []
        notes: list[ReviewComment] = []
        dropped = 0
        failed = 0

        for chunk in chunks:
            try:
                data = self.llm.complete_json(SYSTEM_PROMPT, build_user_prompt(chunk.text, related_code))
            except Exception:
                # One failing chunk should not kill the whole review.
                logger.exception("LLM call failed for chunk with %d file(s)", len(chunk.files))
                failed += 1
                continue

            summary = data.get("summary")
            if isinstance(summary, str) and summary.strip():
                summaries.append(summary.strip())

            raws = data.get("comments") or []
            if reanchor is not None:
                raws = reanchor(raws)
            inline, context, bad = placer.place(raws)
            comments += inline
            notes += context
            dropped += bad

        comments, notes, cut = finalize_comments(comments, notes, self.max_comments)
        dropped += cut

        return ReviewResult(
            summary=" ".join(summaries) or "No significant issues found.",
            comments=comments,
            notes=notes,
            skipped_files=skipped,
            dropped_comments=dropped,
            failed_chunks=failed,
        )


class CommentPlacer:
    """Decides where each LLM comment goes: inline, note, or dropped.

    Shared by the pipeline Reviewer and the AgentReviewer, so both modes
    apply exactly the same rules and their evaluation numbers compare fairly.
    """

    def __init__(self, files: list, position_diff_text: str | None = None) -> None:
        self.commentable = {f.path: f.commentable_lines() for f in files}
        self.added = {f.path: f.added_lines() for f in files}
        self.positionable = None
        if position_diff_text is not None:
            self.positionable = {f.path: f.commentable_lines() for f in parse_diff(position_diff_text)}

    def place(self, raws: list) -> tuple[list[ReviewComment], list[ReviewComment], int]:
        """Return (inline comments, notes, number dropped)."""
        inline: list[ReviewComment] = []
        notes: list[ReviewComment] = []
        dropped = 0
        for raw in raws:
            comment = _to_comment(raw)
            if comment is None:
                dropped += 1
            elif comment.line in self.added.get(comment.path, set()) and (
                self.positionable is None or comment.line in self.positionable.get(comment.path, set())
            ):
                inline.append(comment)
            elif comment.line in self.commentable.get(comment.path, set()):
                # Unchanged context line: keep it, but out of the inline review.
                notes.append(comment)
            else:
                # Not part of the diff at all; GitHub would reject it.
                dropped += 1
        return inline, notes, dropped


    def off_diff(self, raws: list) -> list[ReviewComment]:
        """Valid comments whose path:line is not in the diff at all.

        These would be dropped by place(); the agent uses this to ask the
        model to re-anchor them before giving up on them.
        """
        out = []
        for raw in raws:
            comment = _to_comment(raw)
            if comment is not None and comment.line not in self.commentable.get(comment.path, set()):
                out.append(comment)
        return out


def finalize_comments(
    comments: list[ReviewComment], notes: list[ReviewComment], limit: int
) -> tuple[list[ReviewComment], list[ReviewComment], int]:
    """Rank and cap both lists; return (comments, notes, number cut)."""
    comments, cut = _rank_and_cap(comments, limit)
    notes, cut_notes = _rank_and_cap(notes, limit)
    return comments, notes, cut + cut_notes


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
