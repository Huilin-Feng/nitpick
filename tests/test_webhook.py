import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from nitpick import main
from nitpick.config import Settings

SECRET = "test-secret"


def _settings():
    return Settings(
        openai_api_key="test", openai_model="m", github_token="t",
        github_app_id=None, github_private_key=None, webhook_secret=SECRET,
        max_tokens_per_chunk=6000, max_comments=10,
    )


@pytest.fixture
def client(monkeypatch):
    queued = []
    # Record jobs instead of calling GitHub and the LLM.
    monkeypatch.setattr(main, "run_job_safely", lambda job, settings: queued.append(job))
    main.app.dependency_overrides[main.get_settings] = _settings
    yield TestClient(main.app), queued
    main.app.dependency_overrides.clear()


def _post(client, event, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sig, "Content-Type": "application/json"},
    )


def _pr_payload(action="opened", draft=False):
    return {
        "action": action,
        "pull_request": {"number": 7, "draft": draft, "head": {"sha": "deadbeef"}},
        "repository": {"name": "demo", "owner": {"login": "octo"}},
        "installation": {"id": 99},
    }


def test_health(client):
    http, _ = client
    assert http.get("/health").json() == {"status": "ok"}


def test_rejects_bad_signature(client):
    http, queued = client
    resp = _post(http, "pull_request", _pr_payload(), secret="wrong")
    assert resp.status_code == 401
    assert queued == []


def test_ping(client):
    http, _ = client
    assert _post(http, "ping", {"zen": "hi"}).json() == {"status": "pong"}


def test_queues_review_for_opened_pr(client):
    http, queued = client
    resp = _post(http, "pull_request", _pr_payload())
    assert resp.status_code == 202
    [job] = queued
    assert (job.owner, job.repo, job.number, job.head_sha, job.installation_id) == ("octo", "demo", 7, "deadbeef", 99)


@pytest.mark.parametrize("action", ["closed", "labeled", "edited"])
def test_ignores_other_actions(client, action):
    http, queued = client
    assert _post(http, "pull_request", _pr_payload(action=action)).json()["status"] == "ignored"
    assert queued == []


def test_ignores_draft_pr(client):
    http, queued = client
    assert _post(http, "pull_request", _pr_payload(draft=True)).json()["status"] == "ignored"
    assert queued == []


def test_ignores_other_events(client):
    http, queued = client
    assert _post(http, "issues", {"action": "opened"}).json()["status"] == "ignored"
    assert queued == []
