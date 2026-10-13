"""Agent review mode: the model may read repository code before commenting.

The pipeline Reviewer only sees the diff. That causes false positives
when the right answer depends on code outside the diff, e.g. whether a
renamed function's callers were updated. Here the model can call
read_file / search_code on a RepoSnapshot, then finishes by calling
submit_review.

The agent never makes a review worse than the pipeline would: if it hits
a limit (steps, tokens), the LLM fails, or the diff is too big for one
request, we return the pipeline's result instead and record why.
"""

import logging

from nitpick.chunker import build_chunks
from nitpick.diff_parser import parse_diff
from nitpick.llm import ToolCall, ToolLLMClient
from nitpick.models import ReviewResult
from nitpick.prompts import AGENT_SYSTEM_PROMPT, build_agent_user_prompt
from nitpick.repo_snapshot import RepoSnapshot, ToolError
from nitpick.reviewer import CommentPlacer, Reviewer, finalize_comments

logger = logging.getLogger(__name__)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read lines of a repository file at the PR's version. Returns numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository root"},
                    "start_line": {"type": "integer", "description": "First line, 1-based (default 1)"},
                    "end_line": {"type": "integer", "description": "Last line, inclusive (default: end of file)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Find lines containing a plain-text string (not a regex). Returns path:line: text.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Text to search for, e.g. a function name"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "Submit the final review. Call exactly once, when you are done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "comments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "line": {"type": "integer"},
                                "severity": {"type": "string", "enum": ["bug", "security", "performance", "style"]},
                                "message": {"type": "string"},
                            },
                            "required": ["path", "line", "severity", "message"],
                        },
                    },
                },
                "required": ["summary", "comments"],
            },
        },
    },
]

LAST_STEP_REMINDER = "This is your last step. Call submit_review now with what you have."


class AgentReviewer:
    def __init__(
        self,
        llm: ToolLLMClient,
        fallback: Reviewer,
        max_steps: int = 8,
        max_tokens: int = 60_000,
        max_tokens_per_chunk: int = 6000,
        max_comments: int = 10,
    ) -> None:
        self.llm = llm
        self.fallback = fallback
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.max_tokens_per_chunk = max_tokens_per_chunk
        self.max_comments = max_comments

    def review(
        self, diff_text: str, snapshot: RepoSnapshot, position_diff_text: str | None = None
    ) -> ReviewResult:
        files = parse_diff(diff_text)
        # max_chunks=2 only to detect "does not fit in one request".
        chunks, skipped = build_chunks(files, self.max_tokens_per_chunk, 2)
        if len(chunks) != 1:
            # Empty diffs and large PRs go to the pipeline, which already
            # knows how to split work across several requests.
            return self._fall_back(diff_text, position_diff_text, "diff_too_large" if chunks else "empty_diff", 0, 0)

        messages: list[dict] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": build_agent_user_prompt(chunks[0].text)},
        ]
        start_tokens = _tokens(self.llm)
        tool_calls = 0

        for step in range(1, self.max_steps + 1):
            if _tokens(self.llm) - start_tokens >= self.max_tokens:
                return self._fall_back(diff_text, position_diff_text, "token_budget", step - 1, tool_calls)
            if step == self.max_steps:
                messages.append({"role": "user", "content": LAST_STEP_REMINDER})

            try:
                turn = self.llm.chat(messages, TOOLS)
            except Exception:
                logger.exception("Agent LLM call failed at step %d", step)
                return self._fall_back(diff_text, position_diff_text, "llm_error", step - 1, tool_calls)

            messages.append(turn.to_message())
            submit = next((c for c in turn.tool_calls if c.name == "submit_review"), None)
            if submit is not None and submit.arguments is not None:
                return self._finish(submit, files, skipped, position_diff_text, step, tool_calls)

            # Every tool call needs a matching tool message, or the API
            # rejects the next request. That includes calls we refuse.
            for call in turn.tool_calls:
                tool_calls += 1
                messages.append({"role": "tool", "tool_call_id": call.id, "content": self._run_tool(call, snapshot)})
            if not turn.tool_calls:
                messages.append({"role": "user", "content": "Reply by calling one of the tools."})

        return self._fall_back(diff_text, position_diff_text, "max_steps", self.max_steps, tool_calls)

    def _run_tool(self, call: ToolCall, snapshot: RepoSnapshot) -> str:
        """Run one lookup tool; any problem becomes a message the model can act on."""
        args = call.arguments
        if args is None:
            if call.name == "submit_review":
                return "Error: submit_review arguments were not valid JSON. Try again."
            return "Error: arguments were not valid JSON. Send a JSON object."
        try:
            if call.name == "read_file":
                path = args.get("path")
                start = args.get("start_line", 1)
                end = args.get("end_line")
                if not isinstance(path, str):
                    return "Error: 'path' must be a string"
                if not _is_int(start) or (end is not None and not _is_int(end)):
                    return "Error: 'start_line' and 'end_line' must be integers"
                return snapshot.read_file(path, start, end)
            if call.name == "search_code":
                query = args.get("query")
                if not isinstance(query, str):
                    return "Error: 'query' must be a string"
                return snapshot.search_code(query)
            return f"Error: unknown tool {call.name!r}"
        except ToolError as exc:
            return f"Error: {exc}"

    def _finish(self, submit, files, skipped, position_diff_text, steps, tool_calls) -> ReviewResult:
        args = submit.arguments
        raws = args.get("comments")
        placer = CommentPlacer(files, position_diff_text)
        comments, notes, dropped = placer.place(raws if isinstance(raws, list) else [])
        comments, notes, cut = finalize_comments(comments, notes, self.max_comments)
        summary = args.get("summary")
        return ReviewResult(
            summary=summary.strip() if isinstance(summary, str) and summary.strip() else "No significant issues found.",
            comments=comments,
            notes=notes,
            skipped_files=skipped,
            dropped_comments=dropped + cut,
            mode="agent",
            agent_steps=steps,
            agent_tool_calls=tool_calls,
        )

    def _fall_back(self, diff_text, position_diff_text, reason, steps, tool_calls) -> ReviewResult:
        logger.info("Agent fell back to pipeline: %s (after %d steps)", reason, steps)
        result = self.fallback.review(diff_text, position_diff_text=position_diff_text)
        result.agent_fallback = reason
        result.agent_steps = steps
        result.agent_tool_calls = tool_calls
        return result


def _tokens(llm) -> int:
    # Fakes without usage tracking count as zero, so the budget never trips.
    return getattr(llm, "total_tokens", 0)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
