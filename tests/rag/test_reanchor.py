from nitpick.rag import RagContextBuilder
from nitpick.rag.embedder import HashEmbedder
from nitpick.rag.store import InMemoryStore


def _builder(sample_repo, pr_diff):
    rag = RagContextBuilder.from_repo(sample_repo, "o/r", "abc", embedder=HashEmbedder(),
                                      store=InMemoryStore())
    rag.build(pr_diff)
    return rag


def test_comment_on_retrieved_caller_moves_to_changed_line(sample_repo, pr_diff):
    rag = _builder(sample_repo, pr_diff)
    caller = next(r.chunk for r in rag.last_result.chunks if r.chunk.path == "checkout.py")
    raw = {"path": "checkout.py", "line": caller.start_line + 1, "severity": "bug",
           "message": "still calls calculate_total without currency"}
    [moved] = rag.reanchor([raw])
    # conftest PR: the def line of calculate_total is the first added line (new line 5)
    assert (moved["path"], moved["line"]) == ("orders.py", 5)
    assert moved["message"].startswith(f"checkout.py line {caller.start_line + 1}: ")
    assert rag.last_reanchored == 1
    assert raw["path"] == "checkout.py"  # input not mutated


def test_comments_on_diff_or_unknown_paths_are_untouched(sample_repo, pr_diff):
    rag = _builder(sample_repo, pr_diff)
    on_diff = {"path": "orders.py", "line": 5, "severity": "bug", "message": "x"}
    unknown = {"path": "nowhere.py", "line": 1, "severity": "bug", "message": "y"}
    bad_line = {"path": "checkout.py", "line": "six", "severity": "bug", "message": "z"}
    assert rag.reanchor([on_diff, unknown, bad_line, "junk"]) == [on_diff, unknown, bad_line, "junk"]
    assert rag.last_reanchored == 0


def test_build_resets_the_counter(sample_repo, pr_diff):
    rag = _builder(sample_repo, pr_diff)
    caller = next(r.chunk for r in rag.last_result.chunks if r.chunk.path == "checkout.py")
    rag.reanchor([{"path": "checkout.py", "line": caller.start_line, "severity": "bug", "message": "m"}])
    rag.build(pr_diff)
    assert rag.last_reanchored == 0
