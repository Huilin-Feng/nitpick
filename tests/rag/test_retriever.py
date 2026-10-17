from nitpick.rag import RagContextBuilder
from nitpick.rag.embedder import HashEmbedder
from nitpick.rag.index import build_index
from nitpick.rag.models import Chunk
from nitpick.rag.retriever import RetrievalConfig, Retriever
from nitpick.rag.store import InMemoryStore


def _retriever(repo, **cfg):
    store, emb = InMemoryStore(), HashEmbedder()
    build_index(repo, store, emb)
    return Retriever(store, emb, RetrievalConfig(**cfg))


def test_finds_unchanged_caller_via_symbol(sample_repo, pr_diff):
    result = _retriever(sample_repo).retrieve(pr_diff)
    caller = [r for r in result.chunks if r.chunk.path == "checkout.py" and r.chunk.symbol == "checkout"]
    assert caller and caller[0].reason == "symbol:calculate_total"
    assert result.chunks[0].reason.startswith("symbol:")  # symbol hits ranked first


def test_never_returns_files_in_the_diff(sample_repo, pr_diff):
    result = _retriever(sample_repo, min_semantic_score=0.0).retrieve(pr_diff)
    assert all(r.chunk.path != "orders.py" for r in result.chunks)


def test_semantic_only_mode_reports_embedding_tokens(sample_repo, pr_diff):
    result = _retriever(sample_repo, use_symbols=False, min_semantic_score=0.0).retrieve(pr_diff)
    assert result.chunks and all(r.reason == "semantic" for r in result.chunks)
    assert result.embedding_tokens > 0


def test_symbol_only_mode_makes_no_embedding_calls(sample_repo, pr_diff):
    result = _retriever(sample_repo, use_semantic=False).retrieve(pr_diff)
    assert result.embedding_tokens == 0
    assert {r.reason for r in result.chunks} == {"symbol:calculate_total"}


def test_token_budget_is_respected(sample_repo, pr_diff):
    result = _retriever(sample_repo, token_budget=30, min_semantic_score=0.0).retrieve(pr_diff)
    assert result.context_tokens <= 30
    assert result.dropped_for_budget >= 1


def test_overlapping_chunks_are_deduplicated():
    store, emb = InMemoryStore(), HashEmbedder()
    a = Chunk("x.py", 1, 60, None, "use target_func here\n" * 3)
    b = Chunk("x.py", 51, 110, None, "target_func again\n" * 3)  # overlaps a
    store.add([a, b], emb.embed([a.text, b.text]))
    diff = "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-def target_func():\n+def target_func(x):\n"
    result = Retriever(store, emb, RetrievalConfig(use_semantic=False)).retrieve(diff)
    assert [r.chunk.id for r in result.chunks] == ["x.py:1-60"]


def test_empty_diff_returns_nothing(sample_repo):
    result = _retriever(sample_repo).retrieve("")
    assert result.chunks == [] and result.context_tokens == 0


def test_builder_end_to_end(sample_repo, pr_diff):
    rag = RagContextBuilder.from_repo(sample_repo, "o/r", "abc123",
                                      embedder=HashEmbedder(), store=InMemoryStore())
    text = rag.build(pr_diff)
    assert "## Related code from the repository" in text
    assert "checkout.py" in text and "references `calculate_total`" in text
    assert rag.last_result is not None and rag.index_stats.chunks > 0
