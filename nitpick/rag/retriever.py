"""Hybrid retrieval: symbol matching + semantic search, merged under a token budget.

Why two paths: most cross-file bugs are "function A changed, caller B didn't".
B's code usually doesn't *look* like A's diff, so pure embedding search tends to
miss it. Symbol matching looks in two directions:
  * callers: chunks that mention a function the PR changed ("symbol:<name>")
  * definitions: the code of functions the new lines call ("defines:<name>")
Semantic search covers what symbol matching can't (similar logic, shared
constants, parallel implementations).
"""
from __future__ import annotations

from dataclasses import dataclass

from .diff_utils import changed_paths, extract_changed_symbols, extract_used_names, parse_unified_diff
from .embedder import Embedder
from .models import RetrievalResult, RetrievedChunk, approx_tokens
from .store import VectorStore


@dataclass
class RetrievalConfig:
    use_symbols: bool = True
    use_semantic: bool = True
    k_semantic: int = 5          # top-k per hunk
    max_symbol_hits: int = 6     # per symbol
    max_symbols: int = 10        # cap symbols searched per PR
    max_used_names: int = 10     # cap called/imported names whose definitions are fetched
    min_semantic_score: float = 0.25
    token_budget: int = 3000     # max tokens of retrieved code added to the prompt
    max_hunks: int = 20          # cap embedding calls on huge PRs


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, config: RetrievalConfig | None = None):
        self.store = store
        self.embedder = embedder
        self.config = config or RetrievalConfig()

    def retrieve(self, diff_text: str) -> RetrievalResult:
        cfg = self.config
        files = parse_unified_diff(diff_text)
        exclude = changed_paths(files)  # the diff already shows these files
        candidates: dict[str, RetrievedChunk] = {}
        result = RetrievalResult()

        # 1) symbol matches: highest priority
        if cfg.use_symbols:
            for sym in extract_changed_symbols(files)[:cfg.max_symbols]:
                for chunk in self.store.search_text(sym, exclude, cfg.max_symbol_hits):
                    candidates.setdefault(chunk.id, RetrievedChunk(chunk, 1.0, f"symbol:{sym}"))
            for name in extract_used_names(files)[:cfg.max_used_names]:
                for chunk in self.store.find_symbol(name, exclude, limit=2):
                    candidates.setdefault(chunk.id, RetrievedChunk(chunk, 0.9, f"defines:{name}"))

        # 2) semantic matches per hunk
        semantic: dict[str, RetrievedChunk] = {}
        if cfg.use_semantic:
            queries = [h.query_text() for f in files for h in f.hunks]
            queries = [q for q in queries if q][:cfg.max_hunks]
            if queries:
                before = self.embedder.tokens_used
                vectors = self.embedder.embed(queries)
                result.embedding_tokens = self.embedder.tokens_used - before
                for vec in vectors:
                    for chunk, score in self.store.query(vec, cfg.k_semantic, exclude):
                        if score < cfg.min_semantic_score or chunk.id in candidates:
                            continue
                        prev = semantic.get(chunk.id)
                        if prev is None or score > prev.score:
                            semantic[chunk.id] = RetrievedChunk(chunk, score, "semantic")

        ordered = list(candidates.values()) + sorted(semantic.values(), key=lambda r: r.score, reverse=True)

        # 3) drop chunks overlapping an already-selected chunk, then fill the budget
        used = 0
        for item in ordered:
            if _overlaps_any(item, result.chunks):
                continue
            cost = approx_tokens(item.chunk.text) + 20  # + header line
            if used + cost > cfg.token_budget:
                result.dropped_for_budget += 1
                continue
            result.chunks.append(item)
            used += cost
        result.context_tokens = used
        return result


def _overlaps_any(item: RetrievedChunk, selected: list[RetrievedChunk]) -> bool:
    c = item.chunk
    for s in selected:
        o = s.chunk
        if o.path == c.path and c.start_line <= o.end_line and o.start_line <= c.end_line:
            return True
    return False
