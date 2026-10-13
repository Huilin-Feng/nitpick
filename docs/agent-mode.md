# Agent mode: design, evaluation and decision

The default reviewer is a fixed pipeline: diff → chunks → LLM → validated
comments. It only ever sees the diff. Agent mode (`REVIEW_MODE=agent`) lets
the model look at the rest of the repository before it comments, to catch
problems whose evidence lives outside the diff, such as a caller in another
file that was not updated.

**Status:** agent mode runs in the evaluation harness. The webhook server
still uses the pipeline; wiring agent mode into it needs the PR's head
commit downloaded as a repository snapshot, which is not built yet.

## Design

- **Tools (read-only):** `read_file(path, start_line, end_line)` and
  `search_code(query)` on a `RepoSnapshot`, plus `submit_review(...)` as the
  only way to finish. `tool_choice="required"` means every reply is a tool
  call, so the final answer is always structured.
- **Sandboxing:** every path from the model is resolved and must stay inside
  the snapshot root (`../`, absolute paths and escaping symlinks are
  rejected). Reads are capped per call, binary and large files are refused,
  and search is plain substring matching, never a model-written regex.
- **Limits and fallback:** a step limit, a token budget, and a one-request
  size limit. If any is hit, or the LLM call fails, the review falls back to
  the pipeline and records why (`max_steps`, `token_budget`, `llm_error`,
  `diff_too_large`). The agent never returns less than the pipeline would.
- **Same comment rules as the pipeline:** both modes share `CommentPlacer`
  (inline vs. note vs. dropped), so their numbers compare fairly.
- **Loop hygiene found through traces:** exact repeat calls are answered
  with a pointer instead of being re-run; the model is told its step count;
  the last step asks it to submit.
- **Validation feedback:** if a submission has comments on lines outside the
  diff (GitHub would reject them), the agent gets one chance to move them to
  the changed line that causes the problem.

## Evaluation

21 planted-issue cases (16 original + 5 new context-dependent ones), 3 runs
each. The agent's snapshot for each case is `fixtures/<id>/after/`. Cost uses
list prices per 1M tokens: gpt-4o-mini $0.15 / $0.60, gpt-4.1-mini $0.40 / $1.60.

### gpt-4o-mini (default model)

| metric | pipeline | agent |
|---|---|---|
| recall (inline comments) | 85% | 100% |
| recall incl. summary notes | 88% | 100% |
| strict precision | 100% | 100% |
| false positives per clean PR | 0.07 | 1.00 |
| validity | 100% | 99% |
| avg cost per review | $0.00019 | $0.00127 |
| avg seconds per review | 1.4 | 5.1 |

Split by case type (same runs):

| cases | pipeline | agent |
|---|---|---|
| 13 original bug cases | 39/39 | 39/39 |
| 3 context-dependent bug cases | 2/9 | 9/9 |
| 5 clean cases, comments per run | 0.0–0.3 | 0.7–1.7 |

The agent mode numbers above were measured before the validation-feedback
step was added; at 99% validity it could have applied to at most one of
the 63 agent reviews.

### gpt-4.1-mini (model comparison)

| metric | pipeline | agent |
|---|---|---|
| recall (inline comments) | 90% | 85% |
| strict precision | 64% | 72% |
| false positives per clean PR | 0.13 | 0.00 |
| validity | 92% | 94% |
| avg cost per review | $0.00058 | $0.00120 |
| avg agent steps | – | 1.4 (gpt-4o-mini: 4.3) |
| context-dependent bug cases | 7/9 | 5/9 |

The two models behave in opposite ways in agent mode: gpt-4o-mini looks
things up readily and finds the context bugs, but comments on clean PRs;
gpt-4.1-mini makes no false positives but rarely looks anything up, so it
misses bugs that need a lookup (e.g. the shared-cache mutation, 0/3).

## What each iteration found

1. **The agent always used all 8 steps.** Traces showed the same 4-line
   file read three times and files read in tiny slices. Added duplicate-call
   handling, whole-file reads and a step counter: across the smoke runs,
   average steps fell from 8.0 to 2.0 and cost per review from $0.00136 to
   $0.00038.
2. **False positive on a pure rename.** The model claimed an unchanged
   `return 0.0` was new behavior. Added "report only what this PR
   introduces"; it did not remove that false positive.
3. **Self-check fields did nothing.** Each comment carries
   `introduced_by_this_pr` and `confidence`, and code drops unsure ones. In
   every gpt-4o-mini run it removed 0 comments: the model rates itself
   "high" every time. It is still in the code because the final numbers were measured
   with it.
4. **First full comparison: no gain at 5x cost.** The original benchmark was
   saturated: in `hard_cross_file` the stale caller is part of the diff, so
   the pipeline sees it too. Added 5 cases where the evidence is only outside
   the diff: 3 bugs (stale import after a rename, a percent→fraction unit
   change with an unchanged caller, in-place sort of a shared cache) and 2
   clean look-alikes (a rename with no other users, a dict key guaranteed by
   defaults).
5. **`build_diffs.py` could produce empty diffs.** `copytree` keeps mtimes,
   so an edit that keeps the file size (a rename to an equally long name)
   looked unchanged to git's stat cache. The case validator caught it before
   any LLM call; fixed by re-adding the index, with a regression test.
6. **Clean context cases got hypothetical comments** ("if the config does
   not provide 'timeout'") without the model reading the definition. Added a
   verify-before-warn rule; no measurable change.
7. **Model swap.** gpt-4.1-mini removed the false positives but showed 67%
   validity in agent mode: it commented on the looked-up file, outside the
   diff. Validation feedback raised that to 94%; recall on context bugs
   stayed limited because it seldom looks things up.

## Decision

- **Default: pipeline with gpt-4o-mini.** Full recall on the original cases,
  near-zero false positives, the lowest cost.
- **Agent mode stays opt-in.** It is the only configuration that found all
  context-dependent bugs, at about 7x the cost and with about one extra
  comment per clean PR. It suits repositories where cross-file breakage is
  expensive and some noise is acceptable.
- **Worth trying next:** run the agent only when a PR renames or changes the
  signature of a function, so ordinary PRs keep the pipeline's precision and
  cost.

## Known limitations

- **Location-based scoring.** A comment counts as a hit if it lands within 3
  lines of the planted issue, so a generic warning ("callers may break") on
  the right line scores the same as a verified finding. Example: the
  gpt-4.1-mini pipeline got 3/3 on `ctx_rename_stale_import` without seeing
  the file that still imports the old name. Scoring by message content would
  fix this.
- **Small benchmark.** 21 cases × 3 runs; per-case differences of a few
  tenths of a comment are within run-to-run noise.
- **Token budget is checked before each call,** so one call can overshoot it.
- **Agent mode is not used by the webhook server yet** (see Status).
