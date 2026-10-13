from nitpick.models import Severity
from nitpick.reviewer import Reviewer
from tests.fakes import FakeLLM
from tests.sample_diffs import INCREMENTAL_OUTSIDE_PR, MODIFIED, MULTI


def _comment(path="app/utils.py", line=2, severity="bug", message="Problem"):
    return {"path": path, "line": line, "severity": severity, "message": message}


def test_valid_comment_is_kept():
    llm = FakeLLM([{"summary": "Adds a mode argument.", "comments": [_comment()]}])
    result = Reviewer(llm).review(MODIFIED)
    assert result.summary == "Adds a mode argument."
    assert len(result.comments) == 1
    assert result.comments[0].severity is Severity.BUG
    assert result.dropped_comments == 0


def test_comment_on_line_outside_diff_is_dropped():
    llm = FakeLLM([{"summary": "s", "comments": [_comment(line=99)]}])
    result = Reviewer(llm).review(MODIFIED)
    assert result.comments == []
    assert result.dropped_comments == 1


def test_comment_on_unknown_file_is_dropped():
    llm = FakeLLM([{"summary": "s", "comments": [_comment(path="nope.py")]}])
    assert Reviewer(llm).review(MODIFIED).dropped_comments == 1


def test_malformed_comments_are_dropped():
    bad = [
        "not a dict",
        _comment(severity="critical"),
        _comment(line="two"),
        _comment(line=True),
        _comment(message="   "),
        {"path": "app/utils.py"},
    ]
    result = Reviewer(FakeLLM([{"summary": "s", "comments": bad}])).review(MODIFIED)
    assert result.comments == []
    assert result.dropped_comments == len(bad)


def test_numeric_string_line_is_accepted():
    llm = FakeLLM([{"summary": "s", "comments": [_comment(line="3", severity="STYLE")]}])
    [c] = Reviewer(llm).review(MODIFIED).comments
    assert c.line == 3 and c.severity is Severity.STYLE


def test_comments_sorted_by_severity_and_capped():
    comments = [
        _comment(line=2, severity="style"),
        _comment(line=2, severity="security"),
        _comment(line=3, severity="bug"),
    ]
    result = Reviewer(FakeLLM([{"summary": "s", "comments": comments}]), max_comments=2).review(MODIFIED)
    assert [c.severity for c in result.comments] == [Severity.SECURITY, Severity.BUG]
    assert result.dropped_comments == 1


def test_comment_on_unchanged_line_becomes_note():
    # Line 4 of MODIFIED is an unchanged context line.
    llm = FakeLLM([{"summary": "s", "comments": [_comment(line=4), _comment(line=2)]}])
    result = Reviewer(llm).review(MODIFIED)
    assert [c.line for c in result.comments] == [2]
    assert [n.line for n in result.notes] == [4]
    assert result.dropped_comments == 0


def test_llm_failure_in_one_chunk_does_not_stop_others():
    # Budget fits app/utils.py alone, so MULTI splits into two chunks.
    llm = FakeLLM([
        RuntimeError("timeout"),
        {"summary": "Second chunk ok.", "comments": []},
    ])
    result = Reviewer(llm, max_tokens_per_chunk=55).review(MULTI)
    assert len(llm.calls) == 2
    assert result.failed_chunks == 1
    assert result.summary == "Second chunk ok."


def test_empty_diff_makes_no_llm_calls():
    llm = FakeLLM([])
    result = Reviewer(llm).review("")
    assert llm.calls == []
    assert result.summary == "No significant issues found."


def test_prompt_contains_line_numbers():
    llm = FakeLLM([{"summary": "s", "comments": []}])
    Reviewer(llm).review(MODIFIED)
    assert '    2 + def load(path, mode="r"):' in llm.calls[0]


def test_incremental_line_outside_pr_diff_becomes_note():
    # Line 9 is new in this push, but GitHub cannot place an inline comment
    # on it because it is not part of the PR diff (MODIFIED).
    llm = FakeLLM([{"summary": "s", "comments": [_comment(line=9)]}])
    result = Reviewer(llm).review(INCREMENTAL_OUTSIDE_PR, position_diff_text=MODIFIED)
    assert result.comments == []
    assert [n.line for n in result.notes] == [9]
