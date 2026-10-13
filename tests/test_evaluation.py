from pathlib import Path

from evaluation.scoring import (
    Case, ExpectedIssue, Location, RunScore, comment_matches, load_cases,
    score_comments, summarize, validate_cases,
)
from nitpick.chunker import build_chunks
from nitpick.diff_parser import parse_diff
from nitpick.models import ReviewComment, Severity
from tests.sample_diffs import MODIFIED

CASES_FILE = Path(__file__).parent.parent / "evaluation" / "cases.json"


def _issue(path="app/utils.py", start=2, end=3, category="bug"):
    return ExpectedIssue(category, "note", [Location(path, start, end)])


def _comment(path="app/utils.py", line=2):
    return ReviewComment(path, line, Severity.BUG, "m")


def test_real_cases_file_is_valid():
    # Guards against typos in cases.json, e.g. a line range outside the diff.
    cases = load_cases(CASES_FILE)
    assert len(cases) == 16
    assert validate_cases(cases) == []


def test_baseline_chunker_cannot_see_late_hunk_bug():
    # Documents a known limitation of the naive chunker. When you improve
    # chunking (extension point #1), flip this assertion.
    case = next(c for c in load_cases(CASES_FILE) if c.id == "hard_late_hunk")
    chunks, _ = build_chunks(parse_diff(case.diff_text), max_tokens=6000)
    assert not any("values[index]" in c.text for c in chunks)


def test_validate_reports_line_outside_diff():
    case = Case("x", "", MODIFIED, [_issue(start=50, end=60)])
    assert validate_cases([case]) == ["x: app/utils.py:50-60 is outside the diff"]


def test_validate_reports_unknown_path():
    case = Case("x", "", MODIFIED, [_issue(path="nope.py")])
    assert validate_cases([case]) == ["x: nope.py is not in the diff"]


def test_match_respects_tolerance():
    issue = _issue(start=10, end=12)
    assert comment_matches(_comment(line=7), issue, tolerance=3)
    assert comment_matches(_comment(line=15), issue, tolerance=3)
    assert not comment_matches(_comment(line=16), issue, tolerance=3)
    assert not comment_matches(_comment(path="other.py", line=11), issue, tolerance=3)


def test_match_accepts_any_location():
    issue = ExpectedIssue("bug", "n", [Location("a.py", 1, 1), Location("b.py", 9, 9)])
    assert comment_matches(_comment(path="b.py", line=9), issue, tolerance=0)


def test_score_counts_hits_and_correct_comments():
    case = Case("x", "", MODIFIED, [_issue(start=2, end=2)])
    hit, correct, hits, misses = score_comments(case, [_comment(line=2), _comment(line=40)], tolerance=0)
    assert (hit, correct, hits, misses) == (1, 1, ["bug"], [])


def _score(case_id, run, expected, hit, comments, correct, clean=False, **kw):
    return RunScore(case_id, run, expected, hit, comments, correct, 0, 0, 0, is_clean=clean, **kw)


def test_summarize():
    scores = [
        _score("a", 1, 1, 1, 2, 1, hit_categories=["bug"], prompt_tokens=1_000_000),
        _score("a", 2, 1, 0, 1, 0, missed_categories=["bug"]),
        _score("clean", 1, 0, 0, 2, 0, clean=True),
        _score("clean", 2, 0, 0, 0, 0, clean=True),
    ]
    s = summarize(scores, price_in=0.15, price_out=0.60)
    assert s["recall"] == 0.5
    assert s["recall_per_run"] == [1.0, 0.0]
    assert s["strict_precision"] == 1 / 3
    assert s["false_positives_per_clean_pr"] == 1.0
    assert s["recall_by_category"] == {"bug": 0.5}
    assert abs(s["total_cost_usd"] - 0.15) < 1e-9
