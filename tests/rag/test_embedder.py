from types import SimpleNamespace

from nitpick.rag.embedder import HashEmbedder, OpenAIEmbedder
from nitpick.rag.store import _cosine


class FakeEmbeddingsAPI:
    def __init__(self):
        self.calls = []

    def create(self, model, input):
        self.calls.append(list(input))
        # return items out of order to check sorting by index
        data = [SimpleNamespace(index=i, embedding=[float(i), 1.0]) for i in range(len(input))][::-1]
        return SimpleNamespace(data=data, usage=SimpleNamespace(total_tokens=10 * len(input)))


def test_openai_embedder_batches_orders_and_counts_tokens():
    api = FakeEmbeddingsAPI()
    emb = OpenAIEmbedder(client=SimpleNamespace(embeddings=api), batch_size=2)
    vecs = emb.embed(["a", "b", "c"])
    assert [len(c) for c in api.calls] == [2, 1]
    assert vecs == [[0.0, 1.0], [1.0, 1.0], [0.0, 1.0]]
    assert emb.tokens_used == 30
    assert emb.cost_usd == 30 / 1_000_000 * 0.02


def test_openai_embedder_replaces_empty_strings():
    api = FakeEmbeddingsAPI()
    OpenAIEmbedder(client=SimpleNamespace(embeddings=api)).embed(["", "x"])
    assert api.calls[0][0] == " "  # API rejects empty input


def test_hash_embedder_similarity():
    e = HashEmbedder()
    a, b, c = e.embed(["calculate_total(items)", "total = calculate_total(cart)", "render_banner title"])
    assert _cosine(a, b) > _cosine(a, c)
    assert e.tokens_used > 0
