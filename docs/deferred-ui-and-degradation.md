# Deferred: UI feedback and the degradation contract

Two items noticed while testing the card redesign (commits `5d606e4`,
`f42fd6f`, `d73bb4a`), consciously postponed so Phases 4–7 could land first.
Neither is a defect today. Both were deferred for the same reason: the code
they touch is scheduled to be rewritten by a later phase, and deciding now
means deciding twice.

Read `plan.md` (product spec, user-owned) and `CLAUDE.md` (conventions) first.

---

## Why these were deferred rather than fixed

`plan.md` § Evaluation Criteria weights the evaluation: AI organization/categorization/retrieval
40%, AI/ML techniques 25%, innovation/UX 20%. Phases 4–7 — embeddings, the
relationship graph, the timeline, RAG — are the first 65% and are unbuilt. The
card is in the 20% bucket and is no longer the blocker it was when the brief in
`ui-brief-url-cards.md` was written: the raw `<pre>` is gone and GitHub results
carry real data.

The sharper reason is that **both items live in code a later phase rewrites.**
Item A is in the upload drop zone, which Phase 6 replaces when a real document
list arrives. Item B wants a shared contract across Gemini call sites, and
there is exactly one today. Designing either now means guessing at a shape that
Phases 5–7 would immediately correct.

---

## Item A — the processing state is too quiet

**Observed:** during an ingest the drop zone's label changes from
"Drop files here or click to browse" to "Processing…" and nothing else moves.
The user's words: *"it changes from drop to processing, but it still feels
hidden."*

**Why it reads as hidden.** The only feedback is one line of text swapping
inside a large dashed box, with no motion, no progress, and no change to the
control that was just used. Worse, the wait is genuinely long and *variable*:
categorization is a real Gemini call behind a 6.5-second rate limiter
(`ai/categorizer.py::_RateLimiter`), so a single ingest can take 2–15 seconds
and a batch upload is serialized — ten files is over a minute with no per-file
signal. `busy` is a single boolean in `Upload.jsx`, so all three inputs
disable together and none of them says which one is working.

**Where it lives:** `frontend/src/components/Upload.jsx` — the `busy` state and
the drop-zone label.

**Worth knowing before designing it:**

- Results are prepended one at a time as each request resolves
  (`setResults((prev) => [new, ...prev])`), so a per-item pending placeholder
  would slot in naturally at the top of the list.
- A batch upload loops sequentially and awaits each file, so per-file progress
  ("3 of 10") is available without any backend change.
- There is no upload-progress signal from the server; the wait is dominated by
  the rate limiter and the Gemini round trip, not by bytes transferred. A
  determinate progress bar would be a lie. A skeleton card or an indeterminate
  indicator is honest; a percentage is not.
- The results list is session-only until Phase 6.

**Suggested scope when picked up:** a pending skeleton card at the top of the
results list, per-input busy state rather than one shared boolean, and a count
during batch uploads. Do it *with* Phase 6, when the drop zone is already being
touched.

---

## Item B — no structured reason for a degraded categorization

**Observed:** when the Gemini free tier runs out, every card said

```
Automatic categorization unavailable (AI service error (ResourceExhausted)).
```

The user read this as the site being broken.

**Half-fixed in `d73bb4a`.** The copy now distinguishes the one thing that
matters to a reader — whether the failure clears itself:

```
Not categorized yet — the free AI quota is used up for now, so try again
shortly. Details below came from the filename.
```

**What is still missing.** The reason exists only as English prose inside
`Categorization.summary`. A client that wants to *behave* differently — offer a
retry button for a quota failure but not for a missing API key, auto-retry
after a delay, or badge the card distinctly — has to pattern-match on the
sentence. `confidence == 0.0` says *that* it degraded but not *why* or whether
retrying helps.

**Where it lives:** `ai/categorizer.py::fallback_categorization` and
`_human_reason`; the response models in `routes/upload.py`.

**Sketch, to be confirmed against real callers:**

```python
degraded_reason: Literal[
    "quota", "timeout", "unreachable", "no_api_key", "unreadable_response",
    "no_text"
] | None
retryable: bool
```

**Why it was not added now.** There is one Gemini call site today
(`categorizer.categorize`). Retrofitting one call site is trivial, so the
"cheap now, expensive later" argument does not hold. `plan.md` §4 Module 3 adds
career-path inference and §5 adds a RAG pipeline — both Gemini callers with
their own failure modes. The contract is worth designing when there are three
real callers to design against rather than extrapolated from one.

**Trigger to pick this up:** the moment a second module calls Gemini. That is
Phase 5 (`ai/career_path.py`) or Phase 7 (`ai/rag.py`), whichever lands first.

---

## Related, already known

From the session handoff, unresolved and not covered above:

- **Rate-limit mitigation is only the 6.5s spacer.** `plan.md` § Risk Mitigation lists
  "cache responses, batch processing, queue uploads" — none exist. Quota
  pressure grows as Phases 4/5/7 add call sites, so item B's `retryable` flag
  is most useful alongside an actual retry or cache.
- **No frontend test infrastructure** (no vitest). The card work is verified by
  screenshot only. Two real bugs in `f42fd6f` — an assumed date printed as
  fact, and a meta line truncating away the date at 390px — were caught by
  looking at screenshots, and nothing would catch their regression today.
- **Commit history from GitHub is unimplemented**, though `plan.md` §4 lists
  it. `pushed_at` is carried instead and answers "is this alive?" at no extra
  request cost.
