"""Minimal GitHub REST API client (only what nitpick needs)."""

import re
import time

import httpx

GITHUB_API = "https://api.github.com"
PR_URL = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = PR_URL.match(url.strip())
    if not match:
        raise ValueError(f"Not a GitHub pull request URL: {url}")
    return match.group(1), match.group(2), int(match.group(3))


class GitHubClient:
    def __init__(self, token: str, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(base_url=GITHUB_API, timeout=30)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict:
        resp = self._http.get(
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers={**self._headers, "Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_pull_request_diff(self, owner: str, repo: str, number: int) -> str:
        # Same endpoint, different Accept header: GitHub returns raw diff text.
        resp = self._http.get(
            f"/repos/{owner}/{repo}/pulls/{number}",
            headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
        )
        resp.raise_for_status()
        return resp.text

    def compare_diff(self, owner: str, repo: str, base: str, head: str) -> str | None:
        """Diff of what changed from `base` to `head`.

        Returns "" if nothing changed, or None if `head` is not simply
        `base` plus new commits (force push, rebase, or `base` no longer
        exists). The caller should then fall back to a full review.
        """
        url = f"/repos/{owner}/{repo}/compare/{base}...{head}"
        meta = self._http.get(url, headers={**self._headers, "Accept": "application/vnd.github+json"})
        if meta.status_code == 404:
            return None
        meta.raise_for_status()
        status = meta.json().get("status")
        if status == "identical":
            return ""
        if status != "ahead":
            return None

        resp = self._http.get(url, headers={**self._headers, "Accept": "application/vnd.github.v3.diff"})
        resp.raise_for_status()
        return resp.text

    def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        commit_id: str,
        body: str,
        comments: list[dict],
    ) -> httpx.Response:
        payload = {
            # Pinning the commit makes line numbers refer to the version we reviewed,
            # even if someone pushes a new commit while the review is running.
            "commit_id": commit_id,
            "body": body,
            "event": "COMMENT",
            "comments": comments,
        }
        return self._http.post(
            f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            headers={**self._headers, "Accept": "application/vnd.github+json"},
            json=payload,
        )


def create_app_jwt(app_id: str, private_key: str) -> str:
    """Short-lived JWT that proves we are the GitHub App."""
    import jwt  # PyJWT

    now = int(time.time())
    claims = {
        "iat": now - 60,  # allow for small clock drift
        "exp": now + 9 * 60,  # GitHub allows at most 10 minutes
        "iss": app_id,
    }
    return jwt.encode(claims, private_key, algorithm="RS256")


def get_installation_token(
    app_id: str,
    private_key: str,
    installation_id: int,
    http: httpx.Client | None = None,
) -> str:
    """Exchange the app JWT for a token scoped to one installation."""
    client = http or httpx.Client(base_url=GITHUB_API, timeout=30)
    resp = client.post(
        f"/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {create_app_jwt(app_id, private_key)}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    return resp.json()["token"]
