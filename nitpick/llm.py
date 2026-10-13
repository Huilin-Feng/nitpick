"""LLM client abstraction.

The reviewer only depends on the LLMClient protocol, so tests can pass
in a fake client and never call a real (paid) API.
"""

import json
from typing import Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict:
        ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        # Imported lazily so the rest of the package works without the SDK.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=60)
        self._model = model
        # Running totals, read by the evaluation script to estimate cost.
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def reset_usage(self) -> None:
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete_json(self, system: str, user: str) -> dict:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Forces the model to return syntactically valid JSON.
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        self.usage["calls"] += 1
        if response.usage is not None:
            self.usage["prompt_tokens"] += response.usage.prompt_tokens
            self.usage["completion_tokens"] += response.usage.completion_tokens

        content = response.choices[0].message.content or "{}"
        return json.loads(content)
