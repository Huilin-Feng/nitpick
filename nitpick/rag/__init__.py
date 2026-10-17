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
        self.last_result = self.retriever.retrieve(diff_text)
        return format_related_code(self.last_result)


__all__ = [
    "Chunk", "RetrievedChunk", "RetrievalResult", "RetrievalConfig", "Retriever",
    "Embedder", "OpenAIEmbedder", "HashEmbedder", "VectorStore", "InMemoryStore", "ChromaStore",
    "IndexStats", "build_index", "checkout_at", "shallow_clone_at", "collection_name", "PRESETS", "format_related_code",
    "RagContextBuilder",
]
