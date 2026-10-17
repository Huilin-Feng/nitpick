from nitpick.rag.context import format_related_code
from nitpick.rag.models import Chunk, RetrievalResult, RetrievedChunk


def test_empty_result_gives_empty_string():
    assert format_related_code(RetrievalResult()) == ""


def test_format_labels_and_language():
    res = RetrievalResult(chunks=[
        RetrievedChunk(Chunk("svc/a.py", 3, 9, "Svc.load", "def load(): ..."), 1.0, "symbol:load_user"),
        RetrievedChunk(Chunk("web/b.ts", 1, 4, None, "const x = 1"), 0.734, "semantic"),
    ])
    out = format_related_code(res)
    assert "### svc/a.py:L3-L9 (Svc.load) — references `load_user`" in out
    assert "```python\ndef load(): ...\n```" in out
    assert "### web/b.ts:L1-L4 — similar code (score 0.73)" in out
    assert "```typescript" in out
    assert "Do NOT leave review comments on these snippets" in out
