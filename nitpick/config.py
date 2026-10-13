"""Application settings loaded from environment variables."""

import os
from dataclasses import dataclass


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
    # Hard cap on inline comments per review, so the bot never floods a PR.
    max_comments: int

    @classmethod
    def from_env(cls) -> "Settings":
        private_key = os.getenv("GITHUB_PRIVATE_KEY")
        key_path = os.getenv("GITHUB_PRIVATE_KEY_PATH")
        if not private_key and key_path:
            with open(key_path, encoding="utf-8") as f:
                private_key = f.read()

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            github_token=os.getenv("GITHUB_TOKEN"),
            github_app_id=os.getenv("GITHUB_APP_ID"),
            github_private_key=private_key,
            webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            max_tokens_per_chunk=int(os.getenv("MAX_TOKENS_PER_CHUNK", "6000")),
            max_comments=int(os.getenv("MAX_COMMENTS", "10")),
        )
