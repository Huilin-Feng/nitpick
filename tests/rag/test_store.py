import pytest

from nitpick.rag.models import Chunk
from nitpick.rag.store import ChromaStore, InMemoryStore


def _chunks():
    return [
        Chunk("a.py", 1, 5, "f", "def f():\n    return g()"),
        Chunk("b.py", 1, 5, "g", "def g():\n    return 1"),
        Chunk("c.py", 1, 5, None, "gg = 2  # mentions gg only"),
    ]


def _run_store_contract(store):
    store.add(_chunks(), [[1, 0], [0.9, 0.1], [0, 1]])
    assert store.count() == 3
    top = store.query([1, 0], k=2)
    assert [c.path for c, _ in top] == ["a.py", "b.py"]
    assert top[0][1] == pytest.approx(1.0, abs=1e-6)
    assert [c.path for c, _ in store.query([1, 0], k=2, exclude_paths=["a.py"])] == ["b.py", "c.py"]
    assert [c.path for c in store.search_text("g")] == ["a.py", "b.py"]
    assert [c.path for c in store.search_text("g", exclude_paths=["a.py"])] == ["b.py"]


def test_in_memory_store():
    _run_store_contract(InMemoryStore())


def test_in_memory_store_length_mismatch():
    with pytest.raises(ValueError):
        InMemoryStore().add(_chunks(), [[1, 0]])


def test_chroma_store():
    pytest.importorskip("chromadb")
    import chromadb
    store = ChromaStore("test-collection", client=chromadb.EphemeralClient())
    store.collection.delete(ids=store.collection.get()["ids"] or ["_none_"])
    _run_store_contract(store)
