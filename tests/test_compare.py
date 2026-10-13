import json

import pytest

from evaluation.compare import compare, resolve


def run(label, recall, fp, cost, scores, agent=None):
    summary = {"recall": recall, "recall_with_notes": recall, "strict_precision": 1.0,
               "false_positives_per_clean_pr": fp, "validity": 1.0,
               "avg_cost_usd_per_review": cost, "avg_seconds_per_review": 1.0}
    if agent:
        summary["agent"] = agent
    return {"config": {"label": label}, "summary": summary, "scores": scores}


def s(case_id, hit, total, comments, clean=False):
    return {"case_id": case_id, "expected_hit": hit, "expected_total": total,
            "comments_total": comments, "is_clean": clean}


def test_compare_side_by_side():
    a = run("pipeline", 0.5, 0.33, 0.0002, [s("hard", 1, 2, 1), s("clean", 0, 0, 1, True)])
    b = run("agent", 1.0, 0.0, 0.0004, [s("hard", 2, 2, 1), s("clean", 0, 0, 0, True)],
            agent={"agent_finished_rate": 1.0, "avg_steps": 2.0, "fallbacks": {}})
    out = compare(a, b)
    assert "recall (inline)" in out and "50%" in out and "100%" in out
    assert "$0.00020" in out and "$0.00040" in out
    assert "1/2, 1.0 comments" in out and "2/2, 1.0 comments" in out
    assert "clean, 1.0 comments" in out and "clean, 0.0 comments" in out
    assert "agent finished itself: 100%" in out


def test_resolve_picks_newest_file_for_label(tmp_path):
    (tmp_path / "20261013-080000_agent.json").write_text("{}")
    (tmp_path / "20261013-090000_agent.json").write_text("{}")
    (tmp_path / "20261013-100000_agent-smoke.json").write_text("{}")
    assert resolve("agent", tmp_path).name == "20261013-090000_agent.json"


def test_resolve_unknown_label(tmp_path):
    with pytest.raises(SystemExit):
        resolve("nope", tmp_path)
