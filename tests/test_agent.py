import json

import pytest

import nitpick.agent as agent_module
from nitpick.agent import LAST_STEP_REMINDER, AgentReviewer
from nitpick.llm import ChatTurn, ToolCall
from nitpick.repo_snapshot import RepoSnapshot
from nitpick.reviewer import Reviewer
from tests.fakes import FakeLLM, FakeToolLLM

# Renames total() -> sum_items(); app/api.py (not in the diff) still calls total().
DIFF = """diff --git a/app/services.py b/app/services.py
--- a/app/services.py
+++ b/app/services.py
@@ -1,2 +1,2 @@
-def total(items):
+def sum_items(items):
     return sum(items)
"""

PIPELINE_ANSWER = {"summary": "pipeline", "comments": []}


@pytest.fixture
def snapshot(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "services.py").write_text("def sum_items(items):\n    return sum(items)\n")
    (tmp_path / "app" / "api.py").write_text("from app.services import total\n")
    return RepoSnapshot(tmp_path)


def call(name, args, call_id="c1"):
    raw = args if isinstance(args, str) else json.dumps(args)
    parsed = None if isinstance(args, str) else args
    return ToolCall(call_id, name, parsed, raw)


def submit(comments, summary="done", call_id="s1"):
    return ChatTurn(None, [call("submit_review", {"summary": summary, "comments": comments}, call_id)])


def make_agent(turns, fallback_responses=(PIPELINE_ANSWER,), **kwargs):
    llm = FakeToolLLM(turns, kwargs.pop("tokens_per_call", 0))
    pipeline_llm = FakeLLM(list(fallback_responses))
    agent = AgentReviewer(llm, Reviewer(pipeline_llm), **kwargs)
    return agent, llm, pipeline_llm


BUG = {"path": "app/services.py", "line": 1, "severity": "bug", "message": "api.py still calls total()"}


def test_submit_right_away(snapshot):
    agent, llm, pipeline_llm = make_agent([submit([BUG])])
    result = agent.review(DIFF, snapshot)

    assert result.mode == "agent" and result.agent_fallback is None
    assert (result.agent_steps, result.agent_tool_calls) == (1, 0)
    assert [(c.path, c.line) for c in result.comments] == [("app/services.py", 1)]
    assert result.summary == "done"
    assert pipeline_llm.calls == []  # the pipeline was never used


def test_tool_result_is_sent_back_with_matching_id(snapshot):
    turns = [ChatTurn(None, [call("search_code", {"query": "total"}, "c7")]), submit([BUG])]
    agent, llm, _ = make_agent(turns)
    result = agent.review(DIFF, snapshot)

    second_request = llm.requests[1]
    assistant, tool = second_request[-2], second_request[-1]
    assert assistant["tool_calls"][0]["id"] == "c7"
    assert tool == {"role": "tool", "tool_call_id": "c7", "content": "app/api.py:1: from app.services import total"}
    assert (result.agent_steps, result.agent_tool_calls) == (2, 1)


def test_parallel_tool_calls_each_get_a_result(snapshot):
    turns = [
        ChatTurn(None, [call("read_file", {"path": "app/api.py"}, "a"), call("search_code", {"query": "sum_items"}, "b")]),
        submit([]),
    ]
    agent, llm, _ = make_agent(turns)
    agent.review(DIFF, snapshot)
    tool_msgs = [m for m in llm.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["a", "b"]
    assert tool_msgs[0]["content"] == "1: from app.services import total"


@pytest.mark.parametrize(
    "bad_call, expected",
    [
        (call("read_file", {"path": "../secret"}), "outside the repository"),
        (call("read_file", {"path": "app/api.py", "start_line": "2"}), "must be integers"),
        (call("read_file", {"path": 5}), "'path' must be a string"),
        (call("search_code", {"query": "x"}), "at least 2 characters"),
        (call("read_file", "{broken"), "not valid JSON"),
        (call("delete_repo", {}), "unknown tool"),
        (call("submit_review", "{broken"), "submit_review arguments were not valid JSON"),
    ],
)
def test_bad_tool_calls_become_errors_for_the_model(snapshot, bad_call, expected):
    agent, llm, _ = make_agent([ChatTurn(None, [bad_call]), submit([])])
    result = agent.review(DIFF, snapshot)
    tool_msg = llm.requests[1][-1]
    assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("Error:")
    assert expected in tool_msg["content"]
    assert result.mode == "agent"  # the agent recovered and finished


def test_submitted_comments_use_the_pipeline_rules(snapshot):
    comments = [
        BUG,
        {"path": "app/services.py", "line": 2, "severity": "style", "message": "context line"},
        {"path": "app/api.py", "line": 1, "severity": "bug", "message": "not in the diff"},
        {"path": "app/services.py", "line": 1, "severity": "nonsense", "message": "bad severity"},
    ]
    result = make_agent([submit(comments)])[0].review(DIFF, snapshot)
    assert len(result.comments) == 1
    assert [n.line for n in result.notes] == [2]
    assert result.dropped_comments == 2


def test_max_steps_falls_back_and_reminds_on_last_step(snapshot):
    lookups = [ChatTurn(None, [call("search_code", {"query": "total"}, f"c{i}")]) for i in range(3)]
    agent, llm, pipeline_llm = make_agent(lookups, max_steps=3)
    result = agent.review(DIFF, snapshot)

    assert result.mode == "pipeline" and result.agent_fallback == "max_steps"
    assert (result.agent_steps, result.agent_tool_calls) == (3, 3)
    assert result.summary == "pipeline"
    assert len(pipeline_llm.calls) == 1
    assert llm.requests[2][-1] == {"role": "user", "content": LAST_STEP_REMINDER}
    assert all(LAST_STEP_REMINDER not in str(r) for r in llm.requests[:2])


def test_token_budget_falls_back(snapshot):
    lookups = [ChatTurn(None, [call("search_code", {"query": "total"}, f"c{i}")]) for i in range(5)]
    agent, llm, _ = make_agent(lookups, tokens_per_call=400, max_tokens=1000)
    result = agent.review(DIFF, snapshot)
    # 3 calls use 1200 tokens; the check before call 4 stops the run.
    assert result.agent_fallback == "token_budget"
    assert len(llm.requests) == 3


def test_llm_error_falls_back(snapshot):
    agent, _, pipeline_llm = make_agent([RuntimeError("API down")])
    result = agent.review(DIFF, snapshot)
    assert result.agent_fallback == "llm_error" and result.mode == "pipeline"
    assert len(pipeline_llm.calls) == 1


def test_reply_without_tool_call_gets_nudged(snapshot):
    agent, llm, _ = make_agent([ChatTurn("I think it is fine."), submit([])])
    result = agent.review(DIFF, snapshot)
    assert llm.requests[1][-1] == {"role": "user", "content": "Reply by calling one of the tools."}
    assert result.mode == "agent"


def test_diff_too_large_goes_straight_to_pipeline(snapshot, monkeypatch):
    real = agent_module.build_chunks

    def two_chunks(files, max_tokens, max_chunks):
        chunks, skipped = real(files, max_tokens, max_chunks)
        return chunks * 2, skipped

    monkeypatch.setattr(agent_module, "build_chunks", two_chunks)
    agent, llm, pipeline_llm = make_agent([])
    result = agent.review(DIFF, snapshot)
    assert result.agent_fallback == "diff_too_large"
    assert llm.requests == [] and len(pipeline_llm.calls) == 1


def test_incremental_review_respects_position_diff(snapshot):
    # Line 1 is new in this push but not in the PR diff, so it becomes a note.
    position_diff = DIFF.replace("@@ -1,2 +1,2 @@", "@@ -5,2 +5,2 @@")
    result = make_agent([submit([BUG])])[0].review(DIFF, snapshot, position_diff_text=position_diff)
    assert result.comments == []
    assert [n.line for n in result.notes] == [1]
