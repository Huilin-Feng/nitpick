"""Show exactly what the LLM sees and says for one evaluation case.

    python -m evaluation.debug_case ctx_units_stale_caller            # rag mode
    python -m evaluation.debug_case ctx_units_stale_caller --pipeline # no retrieval

Prints each chunk's user prompt and the raw JSON reply, before CommentPlacer
filters anything, so dropped comments can be inspected.
"""

import argparse
import json

from evaluation.run_eval import ROOT, RagCache, build_embedder, build_llm
from evaluation.scoring import load_cases
from nitpick.config import Settings
from nitpick.reviewer import Reviewer


class SpyLLM:
    def __init__(self, inner) -> None:
        self.inner = inner

    def complete_json(self, system: str, user: str) -> dict:
        data = self.inner.complete_json(system, user)
        print("=" * 30, "USER PROMPT", "=" * 30)
        print(user)
        print("=" * 30, "RAW REPLY", "=" * 32)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return data


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("case_id")
    p.add_argument("--pipeline", action="store_true", help="no retrieved code")
    p.add_argument("--variant", default="hybrid", choices=["hybrid", "semantic", "symbol"])
    args = p.parse_args()

    settings = Settings.from_env()
    case = next(c for c in load_cases(ROOT / "cases.json") if c.id == args.case_id)
    related, rag = "", None
    if not args.pipeline:
        rag = RagCache(build_embedder(settings, False), args.variant, None).get(case)
        related = rag.build(case.diff_text)

    result = Reviewer(SpyLLM(build_llm(settings, False))).review(
        case.diff_text, related_code=related, reanchor=rag.reanchor if rag else None
    )
    print("=" * 30, "AFTER PLACEMENT", "=" * 28)
    print(f"inline {len(result.comments)}  notes {len(result.notes)}  dropped {result.dropped_comments}"
          f"  re-anchored {rag.last_reanchored if rag else 0}")
    for c in result.comments + result.notes:
        print(f"  {c.path}:{c.line}  {c.message[:120]}")
    print("expected:", json.dumps([e.__dict__ if hasattr(e, "__dict__") else e for e in case.expected],
                                 default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
