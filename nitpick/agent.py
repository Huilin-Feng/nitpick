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

import json
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
            "description": (
                "Read a repository file at the PR's version; returns numbered lines. "
                "Omit start_line and end_line to read the whole file in one call."
            ),
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
                                "introduced_by_this_pr": {
                                    "type": "boolean",
                                    "description": "false if the old code already behaved this way",
                                },
                                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                            },
                            "required": [
                                "path", "line", "severity", "message", "introduced_by_this_pr", "confidence",
                            ],
                        },
                    },
                },
                "required": ["summary", "comments"],
            },
        },
    },
]

LAST_STEP_REMINDER = "This is your last step. Call submit_review now with what you have."


def step_status(step: int, max_steps: int) -> str:
    # Without this the model has no idea how many steps it has used, and
    # in testing it kept looking things up until the step limit stopped it.
    return f"Step {step} of {max_steps} used. Submit as soon as you can decide; you do not need every step."


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
        self._trace = []
        self._resubmitted = False
        # (tool name, canonical arguments) -> step it was first made in.
        self._seen: dict[tuple[str, str], int] = {}
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
            rejection = None
            if submit is not None and submit.arguments is not None:
                rejection = self._off_diff_feedback(submit, files, position_diff_text, step)
                if rejection is None:
                    return self._finish(submit, files, skipped, position_diff_text, step, tool_calls)

            # Every tool call needs a matching tool message, or the API
            # rejects the next request. That includes calls we refuse.
            for call in turn.tool_calls:
                tool_calls += 1
                if call is submit and rejection is not None:
                    content, label = rejection, " [resubmit requested]"
                else:
                    content, duplicate = self._run_or_reuse(call, snapshot, step)
                    label = " [duplicate]" if duplicate else ""
                self._trace.append(f"{call.name}({call.raw_arguments[:150]})" + label)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
            if not turn.tool_calls:
                messages.append({"role": "user", "content": "Reply by calling one of the tools."})
            elif step < self.max_steps - 1:
                # The step before the last gets LAST_STEP_REMINDER instead.
                messages.append({"role": "user", "content": step_status(step, self.max_steps)})

        return self._fall_back(diff_text, position_diff_text, "max_steps", self.max_steps, tool_calls)

    def _off_diff_feedback(self, submit, files, position_diff_text, step) -> str | None:
        """Ask once for a resubmit if comments point at lines outside the diff.

        Found in evaluation: the agent often finds a real problem in a file
        it looked up (e.g. a stale caller) and comments there. GitHub only
        accepts comments on diff lines, so those comments were silently
        dropped. Telling the model which ones and why lets it re-anchor
        them to the changed line. At most one retry, and never on the last
        step, so this cannot loop.
        """
        if self._resubmitted or step >= self.max_steps:
            return None
        raws = submit.arguments.get("comments")
        kept, _ = _self_check(raws if isinstance(raws, list) else [])
        off = CommentPlacer(files, position_diff_text).off_diff(kept)
        if not off:
            return None
        self._resubmitted = True
        listed = "\n".join(f"- {c.path}:{c.line}: {c.message[:120]}" for c in off)
        return (
            "Not submitted. These comments point at lines that are not in the diff, so GitHub would reject them:\n"
            f"{listed}\n"
            "Move each one to the changed line in the diff that causes the problem, and mention the affected "
            "file and line in the message. Then call submit_review again with ALL comments."
        )

    def _run_or_reuse(self, call: ToolCall, snapshot: RepoSnapshot, step: int) -> tuple[str, bool]:
        """Run a tool call, unless the exact same call was already made.

        Found in evaluation traces: the model re-read the same 4-line file
        three times and repeated searches. The earlier result is still in the
        conversation, so we answer with a short pointer instead of resending it.
        """
        if call.arguments is not None:
            key = (call.name, json.dumps(call.arguments, sort_keys=True))
            if key in self._seen:
                first = self._seen[key]
                return (f"You already made this exact call at step {first}; its result is earlier in the "
                        "conversation. Do not repeat calls. Decide with what you have."), True
            self._seen[key] = step
        return self._run_tool(call, snapshot), False

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
        kept, filtered = _self_check(raws if isinstance(raws, list) else [])
        placer = CommentPlacer(files, position_diff_text)
        comments, notes, dropped = placer.place(kept)
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
            agent_trace=list(self._trace),
            agent_filtered=filtered,
        )

    def _fall_back(self, diff_text, position_diff_text, reason, steps, tool_calls) -> ReviewResult:
        logger.info("Agent fell back to pipeline: %s (after %d steps)", reason, steps)
        result = self.fallback.review(diff_text, position_diff_text=position_diff_text)
        result.agent_fallback = reason
        result.agent_steps = steps
        result.agent_tool_calls = tool_calls
        result.agent_trace = list(self._trace)
        return result


def _self_check(raws: list) -> tuple[list, int]:
    """Drop comments the model itself marked as unsure or not caused by the PR.

    Asking for these two fields makes the model state its claim explicitly,
    and filtering in code is deterministic. Comments that omit the fields
    are kept, so a model that ignores the schema degrades to the old behavior.
    Returns (kept, number filtered).
    """
    kept = []
    for raw in raws:
        if isinstance(raw, dict) and (
            raw.get("introduced_by_this_pr") is False or raw.get("confidence") in ("medium", "low")
        ):
            continue
        kept.append(raw)
    return kept, len(raws) - len(kept)


def _tokens(llm) -> int:
    # Fakes without usage tracking count as zero, so the budget never trips.
    return getattr(llm, "total_tokens", 0)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
