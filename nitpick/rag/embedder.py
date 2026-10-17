"""Embedding backends.

`OpenAIEmbedder` is the real one. `HashEmbedder` is a deterministic, offline
stand-in used in tests (and handy for a dry run without an API key).
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, Sequence


class Embedder(Protocol):
    dim: int
    tokens_used: int  # running total, for cost reporting

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Wraps the OpenAI embeddings API. The client is injectable so tests can mock it."""

    # USD per 1M tokens; update if pricing changes
    PRICES = {"text-embedding-3-small": 0.02, "text-embedding-3-large": 0.13}
    DIMS = {"text-embedding-3-small": 1536, "text-embedding-3-large": 3072}

    def __init__(self, model: str = "text-embedding-3-small", client=None, batch_size: int = 100):
        if client is None:
            from openai import OpenAI  # imported lazily so tests don't need the package
            client = OpenAI()
        self.client = client
        self.model = model
        self.batch_size = batch_size
        self.dim = self.DIMS.get(model, 1536)
        self.tokens_used = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[i:i + self.batch_size]]
            resp = self.client.embeddings.create(model=self.model, input=batch)
            # API returns items with .index; sort to be safe
            for item in sorted(resp.data, key=lambda d: d.index):
                vectors.append(list(item.embedding))
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self.tokens_used += getattr(usage, "total_tokens", 0) or 0
        return vectors

    @property
    def cost_usd(self) -> float:
        return self.tokens_used / 1_000_000 * self.PRICES.get(self.model, 0.0)


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _split_identifier(tok: str) -> list[str]:
    parts = [p for p in re.split(r"_|(?<=[a-z])(?=[A-Z])", tok) if p]
    return [tok.lower()] + [p.lower() for p in parts]


class HashEmbedder:
    """Bag-of-identifiers hashed into a fixed-size vector, L2-normalized.

    Not semantic at all, but texts sharing identifiers get high cosine
    similarity, which is enough to test the retrieval plumbing offline.
    """

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.tokens_used = 0

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            toks = _TOKEN_RE.findall(text)
            self.tokens_used += len(toks)
            for tok in toks:
                for piece in _split_identifier(tok):
                    h = int(hashlib.md5(piece.encode()).hexdigest(), 16)
                    vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
