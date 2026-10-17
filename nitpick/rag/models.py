"""Shared data structures for the RAG module."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chunk:
    """A slice of a source file that gets embedded and stored."""

    path: str            # repo-relative path, always with forward slashes
    start_line: int      # 1-based, inclusive
    end_line: int        # 1-based, inclusive
    symbol: str | None   # e.g. "UserService.get_user", "<module>", or None for line windows
    text: str

    @property
    def id(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def embedding_text(self, max_chars: int = 8000) -> str:
        """Text sent to the embedding model. Path and symbol help retrieval."""
        header = f"# file: {self.path}"
        if self.symbol:
            header += f"  symbol: {self.symbol}"
        return f"{header}\n{self.text}"[:max_chars]


@dataclass
class RetrievedChunk:
    chunk: Chunk
    score: float   # 1.0 for symbol hits, cosine similarity for semantic hits
    reason: str    # "symbol:<name>" or "semantic"


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk] = field(default_factory=list)
    context_tokens: int = 0      # approx tokens the retrieved code adds to the prompt
    embedding_tokens: int = 0    # tokens spent embedding the query hunks
    dropped_for_budget: int = 0  # candidates that did not fit the token budget


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars per token). Good enough for budgeting."""
    return max(1, len(text) // 4)
