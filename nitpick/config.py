"""Application settings loaded from environment variables."""

import os
from dataclasses import dataclass

REVIEW_MODES = ("pipeline", "agent")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    # Personal access token: simplest option for local development.
    github_token: str | None
    # GitHub App credentials: used when running as a webhook service.
    github_app_id: str | None
    github_private_key: str | None
    webhook_secret: str | None
    # Rough upper bound of diff tokens sent to the LLM in one request.
    max_tokens_per_chunk: int
    # Maximum LLM calls per review; limits cost on very large PRs.
    max_chunks: int
    # SQLite file that remembers which commits were reviewed.
    db_path: str
    # Hard cap on inline comments per review, so the bot never floods a PR.
    max_comments: int
    # The fields below have defaults so existing code that builds Settings
    # directly (tests, scripts) keeps working without changes.
    # "pipeline": fixed diff -> chunks -> LLM flow. "agent": the model may
    # read repository files before commenting.
    review_mode: str = "pipeline"
    # Agent brakes: whichever limit is hit first ends the agent run, and
    # the review falls back to the pipeline result.
    agent_max_steps: int = 8
    agent_max_tokens: int = 60_000

    @classmethod
    def from_env(cls) -> "Settings":
        private_key = os.getenv("GITHUB_PRIVATE_KEY")
        key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not private_key and key_path:
            with open(key_path, encoding="utf-8") as f:
                private_key = f.read()

        review_mode = os.getenv("REVIEW_MODE", "pipeline").strip().lower()
        if review_mode not in REVIEW_MODES:
            # Fail at startup rather than silently running the wrong mode.
            raise ValueError(f"REVIEW_MODE must be one of {REVIEW_MODES}, got {review_mode!r}")

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            github_token=os.getenv("GITHUB_TOKEN"),
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_private_key=private_key,
            webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            max_tokens_per_chunk=int(os.getenv("MAX_TOKENS_PER_CHUNK", "6000")),
            max_chunks=int(os.getenv("MAX_CHUNKS", "5")),
            max_comments=int(os.getenv("MAX_COMMENTS", "10")),
            db_path=os.getenv("NITPICK_DB_PATH", "nitpick.db"),
            review_mode=review_mode,
            agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "8")),
            agent_max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "60000")),
        )
