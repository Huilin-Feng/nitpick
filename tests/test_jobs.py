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
        max_tokens_per_chunk=6000,
        max_comments=10,
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
