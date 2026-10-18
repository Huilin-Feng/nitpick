"""The RAG section reaches the pipeline prompt, and pipeline mode is unchanged."""

from nitpick.prompts import build_user_prompt
from nitpick.reviewer import Reviewer

DIFF = """diff --git a/app/x.py b/app/x.py
--- a/app/x.py
+++ b/app/x.py
@@ -1,2 +1,2 @@
 def f():
-    return 1
+    return 2
"""


class CapturingLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.prompts.append(user)
        return {"summary": "", "comments": []}


def test_build_user_prompt_without_related_code_is_unchanged():
    assert build_user_prompt("DIFF") == "Review the following diff.\n\nDIFF"


def test_build_user_prompt_appends_related_code_after_the_diff():
    assert build_user_prompt("DIFF", "## Related").endswith("DIFF\n\n## Related")


def test_reviewer_passes_related_code_to_every_chunk():
    llm = CapturingLLM()
    Reviewer(llm).review(DIFF, related_code="## Related code\nRELATED")
    assert llm.prompts and all("RELATED" in p for p in llm.prompts)


def test_reviewer_default_has_no_related_section():
    llm = CapturingLLM()
    Reviewer(llm).review(DIFF)
    assert llm.prompts and "Related code" not in llm.prompts[0]


class OffDiffLLM:
    """Comments on a file that is not in the diff, like the model does with retrieved code."""

    def complete_json(self, system: str, user: str) -> dict:
        return {"summary": "", "comments": [
            {"path": "app/caller.py", "line": 6, "severity": "bug", "message": "stale caller"}
        ]}


def test_reanchor_hook_turns_a_dropped_comment_into_an_inline_one():
    def reanchor(raws):
        return [dict(r, path="app/x.py", line=2) for r in raws]

    without = Reviewer(OffDiffLLM()).review(DIFF)
    assert without.comments == [] and without.dropped_comments == 1

    with_hook = Reviewer(OffDiffLLM()).review(DIFF, reanchor=reanchor)
    assert [(c.path, c.line) for c in with_hook.comments] == [("app/x.py", 2)]
