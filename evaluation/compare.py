"""Compare two saved evaluation runs side by side.

    python -m evaluation.compare pipeline-v2 agent-v2

Each argument is a label (the newest results file with that label is used)
or a path to a results JSON file.
"""

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results"

# (key in summary, display name, format)
METRICS = [
    ("recall", "recall (inline)", "pct"),
    ("recall_with_notes", "recall incl. notes", "pct"),
    ("strict_precision", "strict precision", "pct"),
    ("false_positives_per_clean_pr", "false positives / clean PR", "num"),
    ("validity", "validity", "pct"),
    ("avg_cost_usd_per_review", "avg cost / review (USD)", "usd"),
    ("avg_seconds_per_review", "avg seconds / review", "num"),
]


def resolve(arg: str, results_dir: Path = RESULTS) -> Path:
    path = Path(arg)
    if path.suffix == ".json" and path.exists():
        return path
    matches = sorted(results_dir.glob(f"*_{arg}.json"))
    if not matches:
        raise SystemExit(f"No results file for label {arg!r} in {results_dir}")
    return matches[-1]  # file names start with a timestamp, so the last is newest


def fmt(value, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value:.0%}"
    if kind == "usd":
        return f"${value:.5f}"
    return f"{value:.2f}"


def per_case(scores: list[dict]) -> dict[str, str]:
    """case id -> 'hits x/y, c comments' (or 'clean, c comments')."""
    out: dict[str, str] = {}
    for case_id in dict.fromkeys(s["case_id"] for s in scores):
        rs = [s for s in scores if s["case_id"] == case_id]
        comments = sum(s["comments_total"] for s in rs) / len(rs)
        if rs[0]["is_clean"]:
            out[case_id] = f"clean, {comments:.1f} comments"
        else:
            hit = sum(s["expected_hit"] for s in rs)
            total = sum(s["expected_total"] for s in rs)
            out[case_id] = f"{hit}/{total}, {comments:.1f} comments"
    return out


def compare(a: dict, b: dict) -> str:
    la, lb = a["config"]["label"], b["config"]["label"]
    lines = [f"{'':<28}{la:>22}{lb:>22}"]
    for key, name, kind in METRICS:
        lines.append(f"{name:<28}{fmt(a['summary'].get(key), kind):>22}{fmt(b['summary'].get(key), kind):>22}")

    agent = b["summary"].get("agent") or a["summary"].get("agent")
    if agent:
        lines.append("")
        lines.append(f"agent finished itself: {agent['agent_finished_rate']:.0%}, "
                     f"avg steps {agent['avg_steps']:.1f}, fallbacks {agent['fallbacks'] or 'none'}")

    lines.append("")
    lines.append(f"{'case':<28}{la:>22}{lb:>22}")
    ca, cb = per_case(a["scores"]), per_case(b["scores"])
    for case_id in dict.fromkeys([*ca, *cb]):
        lines.append(f"{case_id:<28}{ca.get(case_id, '-'):>22}{cb.get(case_id, '-'):>22}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__)
        return 1
    a, b = (json.loads(resolve(x).read_text()) for x in args)
    print(compare(a, b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
