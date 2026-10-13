"""Glue code: fetch a PR, review it, and post the result back to GitHub.

==========================================================================
EXTENSION POINT #2 (your design): idempotency and caching
--------------------------------------------------------------------------
Right now every webhook delivery triggers a full review. That means:
  * GitHub retrying a delivery -> the same PR is reviewed twice
  * pushing a commit that only touches README -> all files reviewed again
Ideas: remember which head_sha was already reviewed, cache LLM results
per file content hash, and only review files that changed since the
last reviewed commit.
==========================================================================
"""

import logging
from dataclasses import dataclass

from nitpick.config import Settings
from nitpick.formatter import format_comment, format_comments_inline, format_summary
from nitpick.github_client import GitHubClient, get_installation_token
from nitpick.llm import LLMClient, OpenAIClient
from nitpick.models import ReviewResult
from nitpick.reviewer import Reviewer

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
) -> ReviewResult:
    gh = gh or GitHubClient(resolve_token(settings, job.installation_id))

    if job.head_sha is None:
        job.head_sha = gh.get_pull_request(job.owner, job.repo, job.number)["head"]["sha"]

    diff = gh.get_pull_request_diff(job.owner, job.repo, job.number)
    result = build_reviewer(settings, llm).review(diff)
    post_review(gh, job, result)

    logger.info(
        "Reviewed %s/%s#%d: %d comments, %d dropped, %d skipped files",
        job.owner, job.repo, job.number,
        len(result.comments), result.dropped_comments, len(result.skipped_files),
    )
    return result
