"""LLM client abstraction.

The reviewer only depends on the LLMClient protocol, so tests can pass
in a fake client and never call a real (paid) API. The review agent
depends on ToolLLMClient in the same way.
"""

import json
from dataclasses import dataclass, field
from typing import Protocol


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str) -> dict:
        ...


@dataclass
class ToolCall:
    id: str
    name: str
    # Parsed JSON arguments, or None if the model sent invalid JSON.
    # The agent reports that back to the model instead of crashing.
    arguments: dict | None
    raw_arguments: str


@dataclass
class ChatTurn:
    """One assistant reply, independent of the OpenAI SDK's types."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict:
        """The assistant message to append to the conversation history.

        The API requires the assistant's tool calls to be in the history
        before the matching tool results, or the next request is rejected.
        """
        message: dict = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}}
                for c in self.tool_calls
            ]
        return message


class ToolLLMClient(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        ...


class OpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", client=None) -> None:
        if client is None:
            # Imported lazily so the rest of the package works without the SDK.
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=60)
        # `client` can be injected so tests never touch the network.
        self._client = client
        self._model = model
        # Running totals, read by the evaluation script to estimate cost.
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def reset_usage(self) -> None:
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def total_tokens(self) -> int:
        return self.usage["prompt_tokens"] + self.usage["completion_tokens"]

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
        self._record_usage(response)
        content = response.choices[0].message.content or "{}"
        return json.loads(content)

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            tools=tools,
            # "required": every reply must be a tool call. The agent can then
            # only finish by calling submit_review, which gives us structured
            # output instead of free text we would have to parse.
            tool_choice="required",
            temperature=0.2,
        )
        self._record_usage(response)
        message = response.choices[0].message
        calls = [_to_tool_call(c) for c in (message.tool_calls or [])]
        return ChatTurn(content=message.content, tool_calls=calls)

    def _record_usage(self, response) -> None:
        self.usage["calls"] += 1
        if response.usage is not None:
            self.usage["prompt_tokens"] += response.usage.prompt_tokens
            self.usage["completion_tokens"] += response.usage.completion_tokens


def _to_tool_call(raw) -> ToolCall:
    text = raw.function.arguments or ""
    try:
        args = json.loads(text) if text else {}
    except json.JSONDecodeError:
        args = None
    if args is not None and not isinstance(args, dict):
        args = None
    return ToolCall(id=raw.id, name=raw.function.name, arguments=args, raw_arguments=text)
