"""Command-line entry point for local use.

    python -m nitpick.cli https://github.com/owner/repo/pull/1          # dry run
    python -m nitpick.cli https://github.com/owner/repo/pull/1 --post   # post to GitHub

Dry run prints the review in the terminal and posts nothing, which is
the safest way to experiment with prompts.
"""

import argparse
import sys

from nitpick.config import Settings
from nitpick.formatter import format_comments_inline
from nitpick.github_client import GitHubClient, parse_pr_url
from nitpick.jobs import ReviewJob, build_reviewer, run_review_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a GitHub pull request with an LLM.")
    parser.add_argument("pr_url")
    parser.add_argument("--post", action="store_true", help="post the review to GitHub")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if not settings.github_token:
        print("GITHUB_TOKEN is not set.", file=sys.stderr)
        return 1

    owner, repo, number = parse_pr_url(args.pr_url)

    if args.post:
        result = run_review_job(ReviewJob(owner, repo, number), settings)
        print(f"Posted review with {len(result.comments)} inline comment(s).")
        return 0

    gh = GitHubClient(settings.github_token)
    diff = gh.get_pull_request_diff(owner, repo, number)
    result = build_reviewer(settings).review(diff)
    print(format_comments_inline(result))
    print(f"\n(dropped comments: {result.dropped_comments})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
