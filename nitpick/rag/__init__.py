"""Retrieval-augmented context for nitpick reviews.

Typical use from the review pipeline:

    from nitpick.rag import RagContextBuilder

    rag = RagContextBuilder.from_repo(repo_dir, repo_slug="owner/repo", commit_sha=base_sha)
    related = rag.build(diff_text)        # str, "" if nothing relevant
    prompt = base_prompt + "\n\n" + related
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .context import format_related_code
from .diff_utils import changed_paths, parse_unified_diff, symbol_anchors
from .embedder import Embedder, HashEmbedder, OpenAIEmbedder
from .index import IndexStats, build_index, checkout_at, collection_name, shallow_clone_at
from .models import Chunk, RetrievalResult, RetrievedChunk
from .retriever import RetrievalConfig, Retriever
from .store import ChromaStore, InMemoryStore, VectorStore

DEFAULT_PERSIST_DIR = Path.home() / ".cache" / "nitpick" / "chroma"

# Retrieval variants for the ablation in the evaluation
PRESETS: dict[str, RetrievalConfig] = {
    "rag-semantic": RetrievalConfig(use_symbols=False),
    "rag-symbol": RetrievalConfig(use_semantic=False),
    "rag-hybrid": RetrievalConfig(),
}


@dataclass
class RagContextBuilder:
    retriever: Retriever
    index_stats: IndexStats
    last_result: RetrievalResult | None = field(default=None)
    last_reanchored: int = 0
    _anchors: dict = field(default_factory=dict, repr=False)
    _diff_paths: set = field(default_factory=set, repr=False)

    @classmethod
    def from_repo(cls, repo_dir: str | Path, repo_slug: str, commit_sha: str,
                  embedder: Embedder | None = None, store: VectorStore | None = None,
                  persist_dir: str | Path | None = DEFAULT_PERSIST_DIR,
                  config: RetrievalConfig | None = None) -> "RagContextBuilder":
        embedder = embedder or OpenAIEmbedder()
        if store is None:
            store = ChromaStore(collection_name(repo_slug, commit_sha),
                                persist_dir=str(persist_dir) if persist_dir else None)
        stats = build_index(repo_dir, store, embedder)
        return cls(Retriever(store, embedder, config), stats)

    def build(self, diff_text: str) -> str:
        files = parse_unified_diff(diff_text)
        self._anchors = symbol_anchors(files)
        self._diff_paths = changed_paths(files)
        self.last_reanchored = 0
        self.last_result = self.retriever.retrieve(diff_text)
        return format_related_code(self.last_result)

    def reanchor(self, raws: list) -> list:
        """Move comments placed on retrieved code onto the changed line that causes them.

        The model often finds a cross-file bug and then comments where the bug
        *shows* (the stale caller), which is outside the diff and would be dropped.
        We know why each snippet was retrieved (which changed symbol it refers to),
        so we can point the comment at that symbol's added line instead.
        Anything we cannot map is returned unchanged (CommentPlacer drops it).
        """
        out = []
        for raw in raws:
            moved = self._reanchor_one(raw)
            if moved is not None:
                self.last_reanchored += 1
                out.append(moved)
            else:
                out.append(raw)
        return out

    def _reanchor_one(self, raw):
        if not isinstance(raw, dict) or self.last_result is None:
            return None
        path, line = raw.get("path"), raw.get("line")
        if isinstance(line, str) and line.isdigit():
            line = int(line)
        if not isinstance(path, str) or path in self._diff_paths or isinstance(line, bool) \
                or not isinstance(line, int):
            return None
        for item in self.last_result.chunks:
            c = item.chunk
            if c.path == path and c.start_line <= line <= c.end_line and ":" in item.reason:
                target = self._anchors.get(item.reason.split(":", 1)[1])
                if target:
                    moved = dict(raw)
                    moved["path"], moved["line"] = target
                    moved["message"] = f"{path} line {line}: {raw.get('message', '')}"
                    return moved
        return None


__all__ = [
    "Chunk", "RetrievedChunk", "RetrievalResult", "RetrievalConfig", "Retriever",
    "Embedder", "OpenAIEmbedder", "HashEmbedder", "VectorStore", "InMemoryStore", "ChromaStore",
    "IndexStats", "build_index", "checkout_at", "shallow_clone_at", "collection_name", "PRESETS", "format_related_code",
    "RagContextBuilder",
]
