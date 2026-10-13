"""Test doubles that replace paid or networked dependencies."""


class FakeLLM:
    """Returns pre-programmed responses and records every prompt it saw."""

    def __init__(self, responses):
        # Each item is either a dict (returned) or an Exception (raised).
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append(user)
        item = self.responses.pop(0) if self.responses else {"summary": "", "comments": []}
        if isinstance(item, Exception):
            raise item
        return item


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGitHub:
    def __init__(self, diff, review_statuses=(200,), compare=None):
        self.diff = diff
        self.review_statuses = list(review_statuses)
        self.reviews = []
        # {(base, head): diff text, "" for no changes, or None for force push}
        self.compare = compare or {}
        self.compare_calls = []

    def get_pull_request(self, owner, repo, number):
        return {"head": {"sha": "abc123"}}

    def get_pull_request_diff(self, owner, repo, number):
        return self.diff

    def compare_diff(self, owner, repo, base, head):
        self.compare_calls.append((base, head))
        return self.compare.get((base, head))

    def create_review(self, owner, repo, number, commit_id, body, comments):
        self.reviews.append({"commit_id": commit_id, "body": body, "comments": comments})
        status = self.review_statuses.pop(0) if self.review_statuses else 200
        return FakeResponse(status)
