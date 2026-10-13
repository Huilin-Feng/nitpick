from nitpick.config import Settings
from nitpick.jobs import ReviewJob, run_review_job
from tests.fakes import FakeGitHub, FakeLLM
from tests.sample_diffs import MODIFIED


def _settings():
    return Settings(
        openai_api_key="test",
        openai_model="test-model",
        github_token="test",
        github_app_id=None,
        github_private_key=None,
        webhook_secret="test",
        max_tokens_per_chunk=6000, max_chunks=5,
        max_comments=10, db_path=':memory:',
    )


def _llm():
    return FakeLLM([{
        "summary": "s",
        "comments": [{"path": "app/utils.py", "line": 2, "severity": "bug", "message": "m"}],
    }])


def test_posts_inline_review_on_pinned_commit():
    gh = FakeGitHub(MODIFIED)
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh)
    [review] = gh.reviews
    assert review["commit_id"] == "sha1"
    assert review["comments"] == [
        {"path": "app/utils.py", "line": 2, "side": "RIGHT", "body": "**🐛 Bug**\n\nm"}
    ]


def test_fetches_head_sha_when_missing():
    gh = FakeGitHub(MODIFIED)
    run_review_job(ReviewJob("o", "r", 1), _settings(), llm=_llm(), gh=gh)
    assert gh.reviews[0]["commit_id"] == "abc123"


def test_falls_back_to_summary_when_inline_rejected():
    gh = FakeGitHub(MODIFIED, review_statuses=[422, 200])
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh)
    assert len(gh.reviews) == 2
    fallback = gh.reviews[1]
    assert fallback["comments"] == []
    assert "`app/utils.py` line 2" in fallback["body"]


# --- review history (ReviewStore) ---------------------------------------

import tempfile
from pathlib import Path

from nitpick.store import ReviewStore
from tests.sample_diffs import INCREMENTAL


def _store():
    return ReviewStore(str(Path(tempfile.mkdtemp()) / "test.db"))


def test_duplicate_delivery_is_reviewed_once():
    store, gh, llm = _store(), FakeGitHub(MODIFIED), _llm()
    job = lambda: ReviewJob("o", "r", 1, head_sha="sha1")
    assert run_review_job(job(), _settings(), llm=llm, gh=gh, store=store) is not None
    assert run_review_job(job(), _settings(), llm=llm, gh=gh, store=store) is None
    assert len(llm.calls) == 1
    assert len(gh.reviews) == 1
    assert store.status("o/r", 1, "sha1") == "done"


def test_new_push_reviews_only_new_changes():
    store = _store()
    gh = FakeGitHub(MODIFIED, review_statuses=[200, 200], compare={("sha1", "sha2"): INCREMENTAL})
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh, store=store)

    llm = FakeLLM([{"summary": "s", "comments": [
        {"path": "app/utils.py", "line": 3, "severity": "style", "message": "new"},
    ]}])
    result = run_review_job(ReviewJob("o", "r", 1, head_sha="sha2"), _settings(), llm=llm, gh=gh, store=store)

    assert gh.compare_calls == [("sha1", "sha2")]
    assert result.since_sha == "sha1"
    # Only the new commit's diff was sent to the LLM.
    assert "# read the whole file" in llm.calls[0]
    assert 'def load(path, mode="r")' in llm.calls[0]
    assert "import os" not in llm.calls[0]
    assert "Reviewed changes since `sha1`" in gh.reviews[1]["body"]
    assert [c["line"] for c in gh.reviews[1]["comments"]] == [3]


def test_force_push_falls_back_to_full_review():
    store = _store()
    gh = FakeGitHub(MODIFIED, review_statuses=[200, 200], compare={("sha1", "sha2"): None})
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh, store=store)

    llm = _llm()
    result = run_review_job(ReviewJob("o", "r", 1, head_sha="sha2"), _settings(), llm=llm, gh=gh, store=store)
    assert result.since_sha is None
    assert "import os" in llm.calls[0]


def test_no_new_changes_posts_nothing():
    store = _store()
    gh = FakeGitHub(MODIFIED, compare={("sha1", "sha2"): ""})
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh, store=store)

    llm = _llm()
    assert run_review_job(ReviewJob("o", "r", 1, head_sha="sha2"), _settings(), llm=llm, gh=gh, store=store) is None
    assert llm.calls == []
    assert len(gh.reviews) == 1
    assert store.status("o/r", 1, "sha2") == "done"


def test_failed_review_is_marked_and_can_be_retried():
    store = _store()
    gh = FakeGitHub(MODIFIED, review_statuses=[500, 200])
    try:
        run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh, store=store)
        raise AssertionError("expected the GitHub error to propagate")
    except RuntimeError:
        pass
    assert store.status("o/r", 1, "sha1") == "failed"

    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=_llm(), gh=gh, store=store)
    assert store.status("o/r", 1, "sha1") == "done"


def test_without_store_every_call_reviews():
    gh, llm = FakeGitHub(MODIFIED, review_statuses=[200, 200]), FakeLLM([])
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=llm, gh=gh)
    run_review_job(ReviewJob("o", "r", 1, head_sha="sha1"), _settings(), llm=llm, gh=gh)
    assert len(llm.calls) == 2
