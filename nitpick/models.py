"""Plain data structures shared across the pipeline.

Kept dependency-free (dataclasses only) so the core logic can be
tested without FastAPI, httpx, or the OpenAI SDK installed.
"""

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"


# Lower number = more important. Used to keep the most useful comments
# when we have to cut down to max_comments.
SEVERITY_PRIORITY = {
    Severity.SECURITY: 0,
    Severity.BUG: 1,
    Severity.PERFORMANCE: 2,
    Severity.STYLE: 3,
}


@dataclass
class DiffLine:
    kind: str  # "+" added, "-" removed, " " unchanged context
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class Hunk:
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # added | modified | deleted | renamed
    is_binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)

    def added_lines(self) -> set[int]:
        """New-file line numbers that this PR actually added or changed."""
        return {
            line.new_lineno
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == "+" and line.new_lineno is not None
        }

    def commentable_lines(self) -> set[int]:
        """New-file line numbers that GitHub accepts inline comments on.

        GitHub only allows review comments on lines that appear in the
        diff, so anything outside this set would be rejected with a 422.
        """
        return {
            line.new_lineno
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind in ("+", " ") and line.new_lineno is not None
        }


@dataclass
class Chunk:
    """A group of files small enough to send to the LLM in one request."""

    files: list[FileDiff]
    text: str
    estimated_tokens: int


@dataclass
class SkippedFile:
    path: str
    reason: str


@dataclass
class ReviewComment:
    path: str
    line: int
    severity: Severity
    message: str


@dataclass
class ReviewResult:
    summary: str
    # Inline comments: only on lines this PR added or changed.
    comments: list[ReviewComment] = field(default_factory=list)
    # Comments on unchanged context lines. These are often opinions about
    # pre-existing code, so they go into the summary instead of inline.
    # They still matter: a stale caller broken by a change elsewhere is
    # usually an unchanged line.
    notes: list[ReviewComment] = field(default_factory=list)
    skipped_files: list[SkippedFile] = field(default_factory=list)
    # Comments the LLM produced but we discarded (e.g. pointed at a line
    # that is not in the diff). Useful for measuring LLM reliability.
    dropped_comments: int = 0
    failed_chunks: int = 0
    # Set for incremental reviews: only changes after this commit were reviewed.
    since_sha: str | None = None
