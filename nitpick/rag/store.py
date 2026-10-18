"""Vector stores.

`ChromaStore` persists to disk and is what you use for real.
`InMemoryStore` has the same interface and is used in tests.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Protocol, Sequence

from .models import Chunk


class VectorStore(Protocol):
    def add(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None: ...
    def query(self, embedding: Sequence[float], k: int,
              exclude_paths: Iterable[str] = ()) -> list[tuple[Chunk, float]]: ...
    def search_text(self, term: str, exclude_paths: Iterable[str] = (),
                    limit: int = 20) -> list[Chunk]: ...
    def find_symbol(self, name: str, exclude_paths: Iterable[str] = (),
                    limit: int = 5) -> list[Chunk]: ...
    def count(self) -> int: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


class InMemoryStore:
    def __init__(self) -> None:
        self._items: list[tuple[Chunk, list[float]]] = []

    def add(self, chunks, embeddings) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        self._items.extend((c, list(e)) for c, e in zip(chunks, embeddings))

    def query(self, embedding, k, exclude_paths=()):
        excluded = set(exclude_paths)
        scored = [(c, _cosine(embedding, e)) for c, e in self._items if c.path not in excluded]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    def search_text(self, term, exclude_paths=(), limit=20):
        excluded = set(exclude_paths)
        hits = [c for c, _ in self._items if c.path not in excluded and _mentions(c.text, term)]
        return hits[:limit]

    def find_symbol(self, name, exclude_paths=(), limit=5):
        excluded = set(exclude_paths)
        return [c for c, _ in self._items if c.path not in excluded and c.symbol == name][:limit]

    def count(self) -> int:
        return len(self._items)


class ChromaStore:
    """Persistent store backed by Chroma. One collection per (repo, commit)."""

    def __init__(self, collection_name: str, persist_dir: str | None = None, client=None):
        if client is None:
            import chromadb  # lazy import
            client = chromadb.PersistentClient(path=persist_dir) if persist_dir else chromadb.Client()
        self.client = client
        self.collection = client.get_or_create_collection(
            name=collection_name, metadata={"hnsw:space": "cosine"}
        )

    @staticmethod
    def _to_meta(c: Chunk) -> dict:
        # Chroma metadata values cannot be None
        return {"path": c.path, "start_line": c.start_line, "end_line": c.end_line,
                "symbol": c.symbol or ""}

    @staticmethod
    def _from_row(doc: str, meta: dict) -> Chunk:
        return Chunk(meta["path"], int(meta["start_line"]), int(meta["end_line"]),
                     meta.get("symbol") or None, doc)

    @staticmethod
    def _where(exclude_paths: Iterable[str], **equals) -> dict | None:
        conditions = [{k: v} for k, v in equals.items()]
        excluded = sorted(set(exclude_paths))
        if excluded:
            conditions.append({"path": {"$nin": excluded}})
        if not conditions:
            return None
        return conditions[0] if len(conditions) == 1 else {"$and": conditions}

    def add(self, chunks, embeddings, batch_size: int = 500) -> None:
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            self.collection.add(
                ids=[c.id for c in batch],
                documents=[c.text for c in batch],
                metadatas=[self._to_meta(c) for c in batch],
                embeddings=[list(e) for e in embeddings[i:i + batch_size]],
            )

    def query(self, embedding, k, exclude_paths=()):
        n = self.count()
        if n == 0:
            return []
        res = self.collection.query(
            query_embeddings=[list(embedding)], n_results=min(k, n),
            where=self._where(exclude_paths), include=["documents", "metadatas", "distances"],
        )
        out = []
        for doc, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            out.append((self._from_row(doc, meta), 1.0 - float(dist)))  # cosine distance -> similarity
        return out

    def search_text(self, term, exclude_paths=(), limit=20):
        res = self.collection.get(
            where=self._where(exclude_paths), where_document={"$contains": term},
            include=["documents", "metadatas"], limit=limit * 3,
        )
        chunks = [self._from_row(d, m) for d, m in zip(res["documents"], res["metadatas"])]
        # $contains is a substring match; keep only whole-word mentions
        return [c for c in chunks if _mentions(c.text, term)][:limit]

    def find_symbol(self, name, exclude_paths=(), limit=5):
        res = self.collection.get(where=self._where(exclude_paths, symbol=name),
                                  include=["documents", "metadatas"], limit=limit)
        return [self._from_row(d, m) for d, m in zip(res["documents"], res["metadatas"])]

    def count(self) -> int:
        return self.collection.count()
