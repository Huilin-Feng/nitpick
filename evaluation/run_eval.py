"""Run nitpick against the evaluation cases and report metrics.

    python -m evaluation.run_eval --dry-run              # free: checks the pipeline only
    python -m evaluation.run_eval --label baseline       # real run, 3 repeats per case
    python -m evaluation.run_eval --label baseline --runs 1 --only bug_off_by_one
    python -m evaluation.run_eval --mode agent --label agent     # agent mode, same cases
    python -m evaluation.run_eval --mode rag --label rag-hybrid  # pipeline + retrieved repo code
    python -m evaluation.run_eval --mode rag --rag-variant symbol --label rag-symbol

Results are printed and saved to evaluation/results/<time>_<label>.json,
including every comment the LLM made, so you can review them by hand.
"""

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from evaluation.scoring import RunScore, load_cases, score_comments, summarize, summarize_agent, validate_cases
from nitpick.agent import AgentReviewer
from nitpick.config import REVIEW_MODES, Settings
from nitpick.llm import ChatTurn, ToolCall
from nitpick.rag import PRESETS, ChromaStore, HashEmbedder, OpenAIEmbedder, RagContextBuilder
from nitpick.repo_snapshot import RepoSnapshot
from nitpick.reviewer import Reviewer

ROOT = Path(__file__).parent
# "rag" is an evaluation-only mode for now, so it is not added to REVIEW_MODES:
# the webhook server cannot be switched to it by accident.
EVAL_MODES = [*REVIEW_MODES, "rag"]
RAG_VARIANTS = {"hybrid": "rag-hybrid", "semantic": "rag-semantic", "symbol": "rag-symbol"}


class SilentLLM:
    """Used by --dry-run: never calls an API and never comments."""

    def complete_json(self, system: str, user: str) -> dict:
        return {"summary": "", "comments": []}

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatTurn:
        # Submit an empty review straight away, so --dry-run also exercises
        # the agent loop end to end without spending anything.
        args = {"summary": "", "comments": []}
        return ChatTurn(None, [ToolCall("dry-run", "submit_review", args, json.dumps(args))])


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", type=Path, default=ROOT / "cases.json")
    p.add_argument("--runs", type=int, default=3, help="repeat each case to average out LLM randomness")
    p.add_argument("--label", default="run", help="name for this experiment, e.g. baseline")
    p.add_argument("--only", nargs="*", help="run only these case ids")
    p.add_argument("--tolerance", type=int, default=3, help="allowed distance in lines for a hit")
    p.add_argument("--max-tokens", type=int, default=None, help="override MAX_TOKENS_PER_CHUNK")
    p.add_argument("--max-chunks", type=int, default=None, help="override MAX_CHUNKS")
    p.add_argument("--mode", choices=EVAL_MODES, default=None, help="override REVIEW_MODE")
    p.add_argument("--rag-variant", choices=list(RAG_VARIANTS), default="hybrid",
                   help="rag mode: which retrieval paths to use (ablation)")
    p.add_argument("--rag-budget", type=int, default=None, help="rag mode: max tokens of retrieved code")
    p.add_argument("--agent-max-steps", type=int, default=None, help="override AGENT_MAX_STEPS")
    p.add_argument("--agent-max-tokens", type=int, default=None, help="override AGENT_MAX_TOKENS")
    # USD per 1M tokens. Defaults are for gpt-4o-mini; check current pricing.
    p.add_argument("--price-in", type=float, default=0.15)
    p.add_argument("--price-out", type=float, default=0.60)
    p.add_argument("--price-embed", type=float, default=0.02, help="text-embedding-3-small, per 1M tokens")
    p.add_argument("--dry-run", action="store_true", help="use a fake LLM; costs nothing")
    p.add_argument("--no-save", action="store_true")
    return p.parse_args(argv)


def build_llm(settings: Settings, dry_run: bool):
    if dry_run:
        return SilentLLM()
    if not settings.openai_api_key:
        sys.exit("OPENAI_API_KEY is not set (or use --dry-run).")
    from nitpick.llm import OpenAIClient

    return OpenAIClient(settings.openai_api_key, settings.openai_model)


def build_embedder(settings: Settings, dry_run: bool):
    if dry_run:
        return HashEmbedder()  # offline and free; retrieval still runs end to end
    from openai import OpenAI

    return OpenAIEmbedder(client=OpenAI(api_key=settings.openai_api_key))


class RagCache:
    """One index per case, built on first use and reused by every run.

    The index is built from the same fixtures/<id>/after/ snapshot the agent
    reads, so both modes see the same repository. Files changed by the PR are
    excluded at query time, so indexing the "after" version leaks nothing.
    """

    def __init__(self, embedder, variant: str, budget: int | None) -> None:
        import chromadb

        self.client = chromadb.EphemeralClient()  # in memory, gone when the process exits
        self.embedder = embedder
        self.config = PRESETS[RAG_VARIANTS[variant]]
        if budget:
            from dataclasses import replace

            self.config = replace(self.config, token_budget=budget)
        self.builders: dict[str, RagContextBuilder] = {}

    def get(self, case) -> RagContextBuilder:
        if case.id not in self.builders:
            store = ChromaStore(f"eval-{case.id}"[:63], client=self.client)
            self.builders[case.id] = RagContextBuilder.from_repo(
                case.snapshot_dir, repo_slug=case.id, commit_sha="after",
                embedder=self.embedder, store=store, config=self.config,
            )
        return self.builders[case.id]


def summarize_rag(rag_runs: list[dict], cache: RagCache, price_embed: float) -> dict:
    n = len(rag_runs) or 1
    index_tokens = sum(b.index_stats.embedding_tokens for b in cache.builders.values())
    query_tokens = sum(r["query_embedding_tokens"] for r in rag_runs)
    return {
        "variant": asdict(cache.config),
        "indexed_chunks": sum(b.index_stats.chunks for b in cache.builders.values()),
        "index_embedding_tokens": index_tokens,
        "index_cost_usd": index_tokens / 1e6 * price_embed,
        "avg_retrieved_chunks": sum(len(r["retrieved"]) for r in rag_runs) / n,
        "avg_context_tokens": sum(r["context_tokens"] for r in rag_runs) / n,
        "avg_query_embedding_cost_usd": query_tokens / 1e6 * price_embed / n,
        "dropped_for_budget": sum(r["dropped_for_budget"] for r in rag_runs),
    }


def print_rag_report(rag: dict) -> None:
    print(f"indexed chunks:         {rag['indexed_chunks']}  (one-time embedding cost ${rag['index_cost_usd']:.5f})")
    print(f"avg retrieved chunks:   {rag['avg_retrieved_chunks']:.1f}  (~{rag['avg_context_tokens']:.0f} prompt tokens)")
    print(f"avg query embed cost:   ${rag['avg_query_embedding_cost_usd']:.6f} per review (not in avg cost above)")
    if rag["dropped_for_budget"]:
        print(f"cut by token budget:    {rag['dropped_for_budget']} chunks in total")


def fmt(value: float | None, pct: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}" if pct else f"{value:.4f}"


def print_agent_report(agent: dict) -> None:
    print(f"agent finished itself:  {fmt(agent['agent_finished_rate'])}  (rest fell back to the pipeline)")
    print(f"used tools:             {fmt(agent['used_tools_rate'])} of reviews")
    print(f"avg steps / tool calls: {agent['avg_steps']:.1f} / {agent['avg_tool_calls']:.1f}")
    print(f"self-check filtered:    {agent['avg_filtered']:.1f} comments per review")
    for reason, count in agent["fallbacks"].items():
        print(f"  fallback[{reason}]: {count}")


def print_report(scores: list[RunScore], summary: dict, label: str) -> None:
    print(f"\n=== {label} ===")
    print(f"{'case':<26}{'hits':>8}{'comments/run':>14}{'notes/run':>11}{'dropped/run':>13}")
    for case_id in dict.fromkeys(s.case_id for s in scores):
        rs = [s for s in scores if s.case_id == case_id]
        if rs[0].is_clean:
            hits = "clean"
        else:
            hits = f"{sum(s.expected_hit for s in rs)}/{sum(s.expected_total for s in rs)}"
        avg_comments = sum(s.comments_total for s in rs) / len(rs)
        avg_notes = sum(s.notes_total for s in rs) / len(rs)
        avg_dropped = sum(s.dropped_comments for s in rs) / len(rs)
        print(f"{case_id:<26}{hits:>8}{avg_comments:>14.1f}{avg_notes:>11.1f}{avg_dropped:>13.1f}")

    print()
    print(f"recall:                 {fmt(summary['recall'])}  per run: {[fmt(r) for r in summary['recall_per_run']]}")
    print(f"recall incl. notes:     {fmt(summary['recall_with_notes'])}")
    print(f"strict precision:       {fmt(summary['strict_precision'])}")
    print(f"false positives/clean:  {summary['false_positives_per_clean_pr']:.2f}  (notes: {summary['notes_per_clean_pr']:.2f})")
    print(f"validity:               {fmt(summary['validity'])}")
    for cat, r in summary["recall_by_category"].items():
        print(f"  recall[{cat}]: {fmt(r)}")
    print(f"failed chunks:          {summary['failed_chunks']}")
    print(f"avg cost per review:    ${summary['avg_cost_usd_per_review']:.5f}")
    print(f"avg seconds per review: {summary['avg_seconds_per_review']:.1f}")
    print(f"total cost:             ${summary['total_cost_usd']:.4f}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c.id in set(args.only)]

    errors = validate_cases(cases)
    if errors:
        print("cases.json has problems:", *errors, sep="\n  ")
        return 1
    if not cases:
        print("No cases selected.")
        return 1

    settings = Settings.from_env()
    max_tokens = args.max_tokens or settings.max_tokens_per_chunk
    max_chunks = args.max_chunks or settings.max_chunks
    mode = args.mode or settings.review_mode
    agent_max_steps = args.agent_max_steps or settings.agent_max_steps
    agent_max_tokens = args.agent_max_tokens or settings.agent_max_tokens

    if mode in ("agent", "rag"):
        missing = [c.id for c in cases if c.snapshot_dir is None]
        if missing:
            print(f"{mode} mode needs fixtures/<id>/after/ for:", *missing, sep="\n  ")
            return 1

    llm = build_llm(settings, args.dry_run)
    reviewer = Reviewer(
        llm, max_tokens_per_chunk=max_tokens, max_comments=settings.max_comments, max_chunks=max_chunks
    )
    # One LLM client serves both the agent and its pipeline fallback, so
    # the usage counted per case includes fallback calls: the cost is honest.
    agent = AgentReviewer(
        llm, fallback=reviewer, max_steps=agent_max_steps, max_tokens=agent_max_tokens,
        max_tokens_per_chunk=max_tokens, max_comments=settings.max_comments,
    )

    rag_cache = RagCache(build_embedder(settings, args.dry_run), args.rag_variant, args.rag_budget) if mode == "rag" else None
    rag_runs: list[dict] = []

    scores: list[RunScore] = []
    for run in range(1, args.runs + 1):
        for case in cases:
            if hasattr(llm, "reset_usage"):
                llm.reset_usage()
            if mode == "rag":
                # Build (or reuse) the case's index before timing starts, so the
                # one-time indexing cost is not counted as review latency.
                rag = rag_cache.get(case)
            started = time.perf_counter()
            if mode == "agent":
                result = agent.review(case.diff_text, RepoSnapshot(case.snapshot_dir))
            elif mode == "rag":
                related = rag.build(case.diff_text)
                result = reviewer.review(case.diff_text, related_code=related)
                rr = rag.last_result
                rag_runs.append({
                    "case_id": case.id,
                    "run": run,
                    "retrieved": [f"{r.chunk.id} {r.reason}" for r in rr.chunks],
                    "context_tokens": rr.context_tokens,
                    "query_embedding_tokens": rr.embedding_tokens,
                    "dropped_for_budget": rr.dropped_for_budget,
                })
            else:
                result = reviewer.review(case.diff_text)
            seconds = time.perf_counter() - started

            hit, correct, hit_cats, missed_cats = score_comments(case, result.comments, args.tolerance)
            hit_with_notes, _, _, _ = score_comments(case, result.comments + result.notes, args.tolerance)
            usage = getattr(llm, "usage", {})
            scores.append(RunScore(
                case_id=case.id,
                run=run,
                expected_total=len(case.expected),
                expected_hit=hit,
                comments_total=len(result.comments),
                comments_correct=correct,
                dropped_comments=result.dropped_comments,
                notes_total=len(result.notes),
                expected_hit_with_notes=hit_with_notes,
                failed_chunks=result.failed_chunks,
                skipped_files=len(result.skipped_files),
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                seconds=seconds,
                is_clean=case.is_clean,
                hit_categories=hit_cats,
                missed_categories=missed_cats,
                comments=[
                    {"path": c.path, "line": c.line, "severity": c.severity.value, "message": c.message, "kind": kind}
                    for kind, group in (("inline", result.comments), ("note", result.notes))
                    for c in group
                ],
                mode=result.mode,
                agent_steps=result.agent_steps,
                agent_tool_calls=result.agent_tool_calls,
                agent_fallback=result.agent_fallback,
                agent_trace=result.agent_trace,
                agent_filtered=result.agent_filtered,
            ))
            agent_info = ""
            if mode == "rag":
                agent_info = f"  retrieved {len(rag_runs[-1]['retrieved'])}"
            if mode == "agent":
                agent_info = f"  steps {result.agent_steps}  tools {result.agent_tool_calls}"
                if result.agent_fallback:
                    agent_info += f"  fallback={result.agent_fallback}"
            print(f"run {run}/{args.runs}  {case.id:<26} hits {hit}/{len(case.expected)}  "
                  f"comments {len(result.comments)}  notes {len(result.notes)}{agent_info}")

    summary = summarize(scores, args.price_in, args.price_out)
    print_report(scores, summary, args.label)
    if mode == "agent":
        summary["agent"] = summarize_agent(scores)
        print_agent_report(summary["agent"])
    if mode == "rag":
        summary["rag"] = summarize_rag(rag_runs, rag_cache, args.price_embed)
        print_rag_report(summary["rag"])
        summary["rag"]["per_review"] = rag_runs

    if not args.no_save:
        out_dir = ROOT / "results"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{args.label}.json"
        config = {
            "label": args.label,
            "mode": mode,
            "agent_max_steps": agent_max_steps if mode == "agent" else None,
            "agent_max_tokens": agent_max_tokens if mode == "agent" else None,
            "rag_variant": args.rag_variant if mode == "rag" else None,
            "model": "dry-run" if args.dry_run else settings.openai_model,
            "max_tokens_per_chunk": max_tokens,
            "max_chunks": max_chunks,
            "max_comments": settings.max_comments,
            "tolerance": args.tolerance,
            "runs": args.runs,
        }
        out.write_text(json.dumps({"config": config, "summary": summary, "scores": [asdict(s) for s in scores]}, indent=2))
        print(f"\nsaved {out.relative_to(ROOT.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
