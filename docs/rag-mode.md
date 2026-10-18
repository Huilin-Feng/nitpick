# RAG mode

The pipeline reviewer only sees the diff, so it cannot catch bugs whose cause is in the
diff but whose symptom is in another file: a renamed function that is still imported
elsewhere, a changed contract whose caller was not updated. The agent mode solves this
with tool calls, at several LLM calls per review. RAG mode tries to get the same
context with **one** LLM call per chunk.

## How it works

1. **Index** (`nitpick/rag/index.py`, `chunker.py`). Source files are split along
   functions and classes with Python's `ast` (other languages: 60-line windows with
   10 lines of overlap), embedded with `text-embedding-3-small`, and stored in Chroma.
   One index per repository snapshot; indexing all 21 evaluation repos costs $0.0002.
2. **Retrieve** (`retriever.py`). Two paths, merged and deduplicated:
   - *Symbol matching*, in two directions: chunks that **reference** a function the PR
     changed (stale callers), and the **definitions** of functions the new code calls
     (to know what a helper actually returns). The enclosing function of a body-only
     change is found from the hunk's context lines, because git leaves the `@@` header
     empty when a hunk starts at the top of a file.
   - *Semantic search*: top-k chunks by cosine similarity to each hunk.

   Files changed by the PR are excluded (the diff already shows them), and the result
   is cut to a token budget (default 3,000 tokens).
3. **Prompt** (`context.py`). Retrieved code is appended after the diff, labelled with
   why it was retrieved, and marked as reference only.
4. **Re-anchor** (`RagContextBuilder.reanchor`). See the next section.

## What the first evaluation run showed

With retrieval alone, recall on `ctx_units_stale_caller` *dropped* from 3/3 (pipeline)
to 0/3. Inspecting the raw replies (`python -m evaluation.debug_case <id>`) showed why:
the model found the bug, but commented on the stale caller in `app/checkout.py`, which is
not part of the diff, so the comment was dropped. A prompt instruction did not change
this behavior.

The fix is deterministic. Every retrieved chunk records *why* it was retrieved (which
changed symbol it references, or which called function it defines). A comment placed on
a retrieved chunk is moved to that symbol's first added line in the diff, and the
original location is kept in the message (`app/checkout.py line 6: ...`). Comments that
cannot be mapped are left alone and dropped as before.

## Results

21 cases (11 single-file bugs, 4 cross-file bugs, 1 large-diff case, 5 clean PRs),
3 runs each, gpt-4o-mini, line tolerance 3.

| Mode | Recall | Precision | False positives per clean PR | Cost per review | LLM calls |
|---|---|---|---|---|---|
| pipeline | 88% | 100% | 0.07 | $0.00020 | 1 per chunk |
| RAG, semantic only | 96% | 100% | 0.07 | $0.00021 | 1 per chunk |
| RAG, symbol only | 100% | 100% | 0.13 | $0.00021 | 1 per chunk |
| RAG, hybrid | 100% | 100% | 0.20 | $0.00021 | 1 per chunk |
| agent | 100% | 100% | 0.80 | $0.00123 | ~4.4 steps |

Average review time: 1.7 s for RAG (symbol only) and 4.1 s for the agent. The agent
finished on its own in 95% of reviews; the rest fell back to the pipeline because the
diff was too large (`hard_late_hunk`).

Takeaways:

- RAG catches the bug the pipeline cannot: `ctx_rename_stale_import` went from 0/3 to
  3/3. Retrieved context also removed the pipeline's false positive on
  `ctx_key_has_default`, because the retrieved `settings.py` shows the key always exists.
- RAG matches the agent's recall at about 1/6 of the cost, with 4x fewer false positives
  on clean PRs (hybrid 0.20 vs 0.80).
- In the ablation, symbol matching does most of the work. Semantic-only results cannot
  be re-anchored (they are not tied to a changed symbol), and in hybrid mode they add
  one extra false positive (`ctx_rename_all_updated`, where an unrelated `post_url` in
  `views.py` was retrieved).

These numbers come from a small, hand-built benchmark; 100% recall means the four
cross-file cases are solved, not that the reviewer finds every bug.

## Limitations

- Symbol matching is name-based: unrelated functions with the same name are retrieved
  (very short and very common names are filtered out).
- Only direct references are found. If A calls B and B calls C, a change to C does not
  retrieve A. The agent can follow such chains; RAG cannot.
- Python is chunked by syntax; other languages by line windows.
- Semantic results are not tied to a symbol, so comments on them cannot be re-anchored.
- RAG mode is evaluation-only for now; the webhook server still runs pipeline or agent.

## Running it

```bash
pip install -r requirements.txt
python -m evaluation.run_eval --mode rag --label rag-hybrid
python -m evaluation.run_eval --mode rag --rag-variant symbol --label rag-symbol
python -m evaluation.debug_case ctx_units_stale_caller   # prompt + raw LLM reply for one case
```
