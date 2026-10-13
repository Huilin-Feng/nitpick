"""Evaluation support for agent mode: snapshot dirs, agent stats, dry runs."""

import json

from evaluation import run_eval
from evaluation.scoring import RunScore, load_cases, summarize_agent

DIFF = """diff --git a/app/services.py b/app/services.py
--- a/app/services.py
+++ b/app/services.py
@@ -1,2 +1,2 @@
-def total(items):
+def sum_items(items):
     return sum(items)
"""


def make_eval_dir(tmp_path, with_snapshot=True):
    (tmp_path / "diffs").mkdir()
    (tmp_path / "diffs" / "rename.diff").write_text(DIFF)
    if with_snapshot:
        after = tmp_path / "fixtures" / "rename" / "after" / "app"
        after.mkdir(parents=True)
        (after / "services.py").write_text("def sum_items(items):\n    return sum(items)\n")
    cases = {"version": 1, "cases": [{"id": "rename", "diff": "diffs/rename.diff", "expected": []}]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    return path


def test_load_cases_finds_the_after_snapshot(tmp_path):
    case = load_cases(make_eval_dir(tmp_path))[0]
    assert case.snapshot_dir == tmp_path / "fixtures" / "rename" / "after"


def test_load_cases_without_fixture(tmp_path):
    assert load_cases(make_eval_dir(tmp_path, with_snapshot=False))[0].snapshot_dir is None


def score(**kwargs):
    base = dict(case_id="c", run=1, expected_total=0, expected_hit=0, comments_total=0,
                comments_correct=0, dropped_comments=0, failed_chunks=0, skipped_files=0)
    return RunScore(**{**base, **kwargs})


def test_summarize_agent():
    scores = [
        score(mode="agent", agent_steps=1, agent_tool_calls=0),
        score(mode="agent", agent_steps=3, agent_tool_calls=2),
        score(mode="pipeline", agent_steps=8, agent_tool_calls=7, agent_fallback="max_steps"),
        score(mode="pipeline", agent_steps=0, agent_fallback="diff_too_large"),
    ]
    stats = summarize_agent(scores)
    assert stats["agent_finished_rate"] == 0.5
    assert stats["used_tools_rate"] == 0.5
    assert stats["avg_steps"] == 3.0
    assert stats["avg_tool_calls"] == 2.25
    assert stats["fallbacks"] == {"diff_too_large": 1, "max_steps": 1}


def test_dry_run_agent_mode(tmp_path, capsys):
    path = make_eval_dir(tmp_path)
    assert run_eval.main(["--cases", str(path), "--dry-run", "--mode", "agent", "--runs", "1", "--no-save"]) == 0
    out = capsys.readouterr().out
    assert "steps 1  tools 0" in out
    assert "agent finished itself:  100%" in out


def test_dry_run_pipeline_mode_unchanged(tmp_path, capsys):
    path = make_eval_dir(tmp_path)
    assert run_eval.main(["--cases", str(path), "--dry-run", "--mode", "pipeline", "--runs", "1", "--no-save"]) == 0
    assert "agent finished" not in capsys.readouterr().out


def test_agent_mode_requires_snapshots(tmp_path, capsys):
    path = make_eval_dir(tmp_path, with_snapshot=False)
    assert run_eval.main(["--cases", str(path), "--dry-run", "--mode", "agent", "--no-save"]) == 1
    assert "rename" in capsys.readouterr().out
