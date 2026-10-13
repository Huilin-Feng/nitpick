"""Pure scoring logic for the evaluation (no LLM, no network).

Kept separate from run_eval.py so it can be unit tested.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from nitpick.diff_parser import parse_diff
from nitpick.models import ReviewComment


@dataclass
class Location:
    path: str
    start: int
    end: int


@dataclass
class ExpectedIssue:
    category: str
    note: str
    # An issue may be acceptably reported in more than one place, e.g. a
    # stale caller can be flagged at the call site or at the changed signature.
    locations: list[Location]


@dataclass
class Case:
    id: str
    description: str
    diff_text: str
    expected: list[ExpectedIssue]
    # fixtures/<id>/after/: the repository as of the PR's head commit.
    # The agent's read_file / search_code tools look at this directory.
    snapshot_dir: Path | None = None

    @property
    def is_clean(self) -> bool:
        return not self.expected


@dataclass
class RunScore:
    case_id: str
    run: int
    expected_total: int
    expected_hit: int
    comments_total: int
    comments_correct: int
    dropped_comments: int
    failed_chunks: int
    skipped_files: int
    notes_total: int = 0
    # Planted issues found if summary notes on unchanged lines also count.
    expected_hit_with_notes: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    is_clean: bool = False
    hit_categories: list[str] = field(default_factory=list)
    missed_categories: list[str] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    # Agent mode bookkeeping (pipeline runs keep the defaults).
    mode: str = "pipeline"
    agent_steps: int = 0
    agent_tool_calls: int = 0
    agent_fallback: str | None = None
    agent_trace: list[str] = field(default_factory=list)
    agent_filtered: int = 0


def load_cases(path: Path) -> list[Case]:
    data = json.loads(path.read_text())
    cases = []
    for raw in data["cases"]:
        expected = [
            ExpectedIssue(
                category=e["category"],
                note=e["note"],
                locations=[Location(l["path"], l["lines"][0], l["lines"][1]) for l in e["locations"]],
            )
            for e in raw["expected"]
        ]
        diff_text = (path.parent / raw["diff"]).read_text()
        # Same layout build_diffs.py reads: fixtures/<id>/after/.
        snapshot_dir = path.parent / "fixtures" / raw["id"] / "after"
        cases.append(Case(
            raw["id"], raw.get("description", ""), diff_text, expected,
            snapshot_dir if snapshot_dir.is_dir() else None,
        ))
    return cases


def validate_cases(cases: list[Case]) -> list[str]:
    """Catch mistakes in cases.json before spending money on LLM calls.

    Every expected location must point at a file in the diff and overlap at
    least one line the reviewer is allowed to comment on. Otherwise the
    issue could never be hit, and recall would be wrong for a silly reason.
    """
    errors = []
    ids = [c.id for c in cases]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errors.append(f"duplicate case id: {dup}")

    for case in cases:
        files = {f.path: f.commentable_lines() for f in parse_diff(case.diff_text)}
        if not files:
            errors.append(f"{case.id}: diff is empty")
        for issue in case.expected:
            for loc in issue.locations:
                if loc.start > loc.end:
                    errors.append(f"{case.id}: line range {loc.start}-{loc.end} is reversed")
                if loc.path not in files:
                    errors.append(f"{case.id}: {loc.path} is not in the diff")
                elif not set(range(loc.start, loc.end + 1)) & files[loc.path]:
                    errors.append(f"{case.id}: {loc.path}:{loc.start}-{loc.end} is outside the diff")
    return errors


def comment_matches(comment: ReviewComment, issue: ExpectedIssue, tolerance: int) -> bool:
    return any(
        comment.path == loc.path and loc.start - tolerance <= comment.line <= loc.end + tolerance
        for loc in issue.locations
    )


def score_comments(case: Case, comments: list[ReviewComment], tolerance: int) -> tuple[int, int, list[str], list[str]]:
    """Return (expected_hit, comments_correct, hit_categories, missed_categories)."""
    hit_categories, missed_categories = [], []
    for issue in case.expected:
        if any(comment_matches(c, issue, tolerance) for c in comments):
            hit_categories.append(issue.category)
        else:
            missed_categories.append(issue.category)

    correct = sum(1 for c in comments if any(comment_matches(c, i, tolerance) for i in case.expected))
    return len(hit_categories), correct, hit_categories, missed_categories


def _ratio(num: float, den: float) -> float | None:
    return None if den == 0 else num / den


def summarize(scores: list[RunScore], price_in: float, price_out: float) -> dict:
    """Aggregate run scores. Prices are USD per 1M tokens."""
    runs = sorted({s.run for s in scores})
    n_runs = max(1, len(runs))

    expected = sum(s.expected_total for s in scores)
    hit = sum(s.expected_hit for s in scores)
    comments = sum(s.comments_total for s in scores)
    correct = sum(s.comments_correct for s in scores if not s.is_clean)
    comments_on_buggy = sum(s.comments_total for s in scores if not s.is_clean)
    clean_comments = sum(s.comments_total for s in scores if s.is_clean)
    clean_notes = sum(s.notes_total for s in scores if s.is_clean)
    n_clean = max(1, len({s.case_id for s in scores if s.is_clean}))
    notes = sum(s.notes_total for s in scores)
    dropped = sum(s.dropped_comments for s in scores)

    per_run_recall = []
    for r in runs:
        rs = [s for s in scores if s.run == r]
        per_run_recall.append(_ratio(sum(s.expected_hit for s in rs), sum(s.expected_total for s in rs)))

    categories: dict[str, list[int]] = {}
    for s in scores:
        for cat in s.hit_categories:
            categories.setdefault(cat, [0, 0])[0] += 1
            categories[cat][1] += 1
        for cat in s.missed_categories:
            categories.setdefault(cat, [0, 0])[1] += 1

    tokens_in = sum(s.prompt_tokens for s in scores)
    tokens_out = sum(s.completion_tokens for s in scores)
    cost = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
    case_runs = max(1, len(scores))

    return {
        "runs": n_runs,
        # Inline comments only: what a developer sees on the changed lines.
        "recall": _ratio(hit, expected),
        "recall_with_notes": _ratio(sum(s.expected_hit_with_notes for s in scores), expected),
        "recall_per_run": per_run_recall,
        # Strict: any comment that does not match a planted issue counts as
        # wrong. Clean PRs are excluded here and reported separately.
        "strict_precision": _ratio(correct, comments_on_buggy),
        "false_positives_per_clean_pr": clean_comments / n_runs / n_clean,
        "notes_per_clean_pr": clean_notes / n_runs / n_clean,
        "notes_per_review": sum(s.notes_total for s in scores) / max(1, len(scores)),
        # Notes are valid comments too (they point at a real diff line).
        "validity": _ratio(comments + notes, comments + notes + dropped),
        "recall_by_category": {k: _ratio(v[0], v[1]) for k, v in sorted(categories.items())},
        "failed_chunks": sum(s.failed_chunks for s in scores),
        "avg_cost_usd_per_review": cost / case_runs,
        "avg_seconds_per_review": sum(s.seconds for s in scores) / case_runs,
        "total_cost_usd": cost,
    }


def summarize_agent(scores: list[RunScore]) -> dict:
    """How the agent behaved: did it finish, how much did it look around."""
    n = max(1, len(scores))
    finished = [s for s in scores if s.mode == "agent"]
    fallbacks: dict[str, int] = {}
    for s in scores:
        if s.agent_fallback:
            fallbacks[s.agent_fallback] = fallbacks.get(s.agent_fallback, 0) + 1
    return {
        # Share of reviews the agent completed itself (no pipeline fallback).
        "agent_finished_rate": len(finished) / n,
        "avg_steps": sum(s.agent_steps for s in scores) / n,
        "avg_tool_calls": sum(s.agent_tool_calls for s in scores) / n,
        # Reviews where the agent read or searched code at least once.
        "used_tools_rate": sum(1 for s in scores if s.agent_tool_calls > 0) / n,
        "fallbacks": dict(sorted(fallbacks.items())),
        # Comments the agent's own self-check removed (unsure / pre-existing).
        "avg_filtered": sum(s.agent_filtered for s in scores) / n,
    }
