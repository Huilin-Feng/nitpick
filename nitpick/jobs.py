"""Glue code: fetch a PR, review it, and post the result back to GitHub.

With a ReviewStore (the webhook server always passes one):
  * each (repo, PR, head commit) is reviewed at most once, so duplicate
    webhook deliveries do not cost a second LLM call
  * after new commits are pushed, only the changes since the last
    reviewed commit are reviewed; after a force push or rebase we fall
    back to reviewing the whole PR
Without a store (CLI --post), every call does a full review.
"""

import logging
from dataclasses import dataclass

from nitpick.config import Settings
from nitpick.formatter import format_comment, format_comments_inline, format_summary
from nitpick.github_client import GitHubClient, get_installation_token
from nitpick.llm import LLMClient, OpenAIClient
from nitpick.models import ReviewResult
from nitpick.reviewer import Reviewer
from nitpick.store import ReviewStore

logger = logging.getLogger(__name__)


@dataclass
class ReviewJob:
    owner: str
    repo: str
    number: int
    head_sha: str | None = None
    installation_id: int | None = None


def resolve_token(settings: Settings, installation_id: int | None) -> str:
    if installation_id and settings.github_app_id and settings.github_private_key:
        return get_installation_token(settings.github_app_id, settings.github_private_key, installation_id)
    if settings.github_token:
        return settings.github_token
    raise RuntimeError("No GitHub credentials: set GITHUB_TOKEN or GitHub App settings.")


def build_reviewer(settings: Settings, llm: LLMClient | None = None) -> Reviewer:
    if llm is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        llm = OpenAIClient(settings.openai_api_key, settings.openai_model)
    return Reviewer(llm, settings.max_tokens_per_chunk, settings.max_comments, settings.max_chunks)


def post_review(gh: GitHubClient, job: ReviewJob, result: ReviewResult) -> None:
    inline = [
        {"path": c.path, "line": c.line, "side": "RIGHT", "body": format_comment(c)}
        for c in result.comments
    ]
    resp = gh.create_review(job.owner, job.repo, job.number, job.head_sha, format_summary(result), inline)

    if resp.status_code == 422 and inline:
        # GitHub rejected at least one inline position. Rather than lose the
        # whole review, post everything as a single summary comment.
        logger.warning("Inline comments rejected for %s/%s#%d, falling back", job.owner, job.repo, job.number)
        resp = gh.create_review(job.owner, job.repo, job.number, job.head_sha, format_comments_inline(result), [])

    resp.raise_for_status()


def run_review_job(
    job: ReviewJob,
    settings: Settings,
    llm: LLMClient | None = None,
    gh: GitHubClient | None = None,
    store: ReviewStore | None = None,
) -> ReviewResult | None:
    """Review one PR commit. Returns None if there was nothing to do."""
    repo_key = f"{job.owner}/{job.repo}"

    def claim() -> bool:
        if store is None or store.try_claim(repo_key, job.number, job.head_sha):
            return True
        logger.info("Skipping %s#%d@%s: already reviewed or in progress", repo_key, job.number, job.head_sha[:7])
        return False

    # Webhooks include the head commit, so duplicates can be rejected before
    # requesting an installation token (found while testing with real
    # deliveries: skipped duplicates were still fetching a token).
    if job.head_sha is not None and not claim():
        return None

    gh = gh or GitHubClient(resolve_token(settings, job.installation_id))

    if job.head_sha is None:
        job.head_sha = gh.get_pull_request(job.owner, job.repo, job.number)["head"]["sha"]
        if not claim():
            return None

    try:
        result = _review(job, settings, llm, gh, store, repo_key)
    except Exception:
        if store is not None:
            store.mark_failed(repo_key, job.number, job.head_sha)
        raise

    if store is not None:
        store.mark_done(repo_key, job.number, job.head_sha)
    return result


def _review(
    job: ReviewJob,
    settings: Settings,
    llm: LLMClient | None,
    gh: GitHubClient,
    store: ReviewStore | None,
    repo_key: str,
) -> ReviewResult | None:
    full_diff = gh.get_pull_request_diff(job.owner, job.repo, job.number)
    reviewer = build_reviewer(settings, llm)

    since = store.last_reviewed_sha(repo_key, job.number) if store is not None else None
    new_changes = gh.compare_diff(job.owner, job.repo, since, job.head_sha) if since else None

    if new_changes == "":
        logger.info("No new changes in %s#%d since %s", repo_key, job.number, since[:7])
        return None

    if new_changes is not None:
        result = reviewer.review(new_changes, position_diff_text=full_diff)
        result.since_sha = since
    else:
        # First review of this PR, or history was rewritten: review everything.
        result = reviewer.review(full_diff)

    post_review(gh, job, result)
    logger.info(
        "Reviewed %s#%d (%s): %d comments, %d notes, %d dropped, %d skipped files",
        repo_key, job.number, f"since {since[:7]}" if result.since_sha else "full",
        len(result.comments), len(result.notes), result.dropped_comments, len(result.skipped_files),
    )
    return result
