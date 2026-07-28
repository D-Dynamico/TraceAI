# TraceAI — AI-Powered Digital Identity System

Transforms fragmented academic and professional documents (certificates, resumes,
project reports, internship letters, portfolios) into a structured, searchable,
intelligent knowledge repository. See [plan.md](plan.md) for the full design.

**LLM:** Gemini 3 Flash (free tier) · **Backend:** FastAPI · **Frontend:** React + Vite + Tailwind

---

## Status

- ✅ Phase 1 — Project setup, file upload, text extraction
- ✅ Phase 2 — Gemini categorization + SQLite storage
- ✅ Phase 3 — URL ingestion + written-response input
- ✅ Phase 4 — Embeddings + ChromaDB + semantic search
- ✅ Phase 5 — Relationship graph + career-path inference (`/api/graph`,
  `/api/career-paths`) + the d3-force graph UI (View 3)
- ✅ Phase 6 — Timeline view + search UI (Views 2 & 4)
- ✅ Phase 7 — RAG pipeline + synthesized answer card (`/api/answer`)
- ✅ Phase 8 — demo seed dataset + "Load Demo Profile" button
- ✅ Phase 10 — deployed: [trace-ai-eta.vercel.app](https://trace-ai-eta.vercel.app)
  (Vercel) + `traceai-api-flmc.onrender.com` (Render)
- 🚧 **Phase 9 — UI polish + edge cases** (current): frontend test suite (vitest,
  142 tests), document delete, explicit "Ask AI" search, the manual category
  override, the warm retheme + free-tier notice, and the two bugs deploying
  surfaced — the graph clipped on phones, and one shared dataset across every
  visitor — done; real-document edge-case testing next
- ⬜ Phase 11 — deliverables (demo video, architecture diagram, thought process)

### Phase 1 capabilities
- Upload PDF / DOCX / PPTX / TXT / images.
- Text extraction via PyMuPDF, python-docx, python-pptx, with OCR fallback
  (pytesseract) for scanned PDFs and images.
- **Original Format Preservation** — see below.
- React upload UI with drag-and-drop, per-file extraction results, warnings,
  and a download-original link.
- *Ahead of schedule:* basic URL ingestion (GitHub + generic web) already works;
  it was split into `github_scraper` / `web_scraper` in Phase 3.

### Phase 2 capabilities
- **Automatic categorization** — every upload is classified by Gemini 3 Flash
  into a document type, category, title, date, summary, skills, organizations,
  people, and tags, with a confidence score. No manual sorting.
- **SQLite persistence** — metadata lands in `documents`, with skills/orgs/people
  written as `entities` rows and tags as `tags` rows, ready for the relationship
  engine to join on in Phase 5.
- **Browsable documents** — `GET /api/documents` (with an optional category
  filter) and `GET /api/documents/{id}` for full detail.
- **Never loses an upload.** A missing API key, a rate limit, a timeout, or
  unparseable model output all degrade to a filename-based guess with
  `confidence = 0.0` and a review warning, rather than failing the request.
  Model output is normalized before storage, so a drifted category or a
  confidence returned as `85` instead of `0.85` does not corrupt the database.
- **Free-tier aware** — calls are serialized to stay within the free tier's
  **5 RPM** (measured from a live 429; see `ai/gemini.py`).

### Phase 3 capabilities
- **URL ingestion that persists.** `POST /api/ingest-url` now runs the same
  categorize-and-store pipeline as an upload — previously it scraped a page and
  threw the result away. Anything not recognised is scraped for visible text.
- **GitHub repos and profiles.** A repo URL pulls description, topics, README,
  and a language breakdown by bytes, plus stars, forks, license, creation date
  and last-push date. A bare profile URL (`github.com/<login>`) used to fall
  through to the generic HTML scraper; it now reaches the user API and returns
  the bio, public repo count, and repo list — **one profile is one document**,
  the same contract as every other input. github.com's own routes
  (`/pricing`, `/explore`, …) are excluded by name, and an unrecognised single
  path segment degrades to the web scraper rather than storing an empty
  profile. The GitHub REST API is called directly rather than via PyGithub,
  which issues its own HTTP and would bypass `url_guard`.
- **Written responses.** `POST /api/ingest-text` accepts a typed achievement
  ("Led the Data Science Club in 2024") with no file at all. Not every
  achievement has a certificate — club leadership, hackathon wins, and
  volunteer work often exist only as memories.
- **SSRF protection.** User-supplied URLs are validated before every request:
  http/https only, and the hostname must resolve exclusively to publicly
  routable addresses. Redirects are followed manually so each hop is
  re-validated, and response bodies are capped at 5 MB. Without this,
  `http://169.254.169.254/latest/meta-data/` would be fetchable — and its body
  returned to the caller — the moment the app is deployed. See
  `ingestion/url_guard.py`.
- **Fileless documents.** URL and text-entry documents have no original file, so
  `original_path` is empty and no sidecar is written. `checksum` is the SHA-256
  of the text itself, which pins *which* snapshot of a page was ingested.
  Preservation still applies in full to uploaded files.
- **The UI now shows what the AI did.** Every result card carries the Gemini
  title, a color-coded category badge, a confidence meter, the summary, and
  extracted skills / organizations / people / tags. Before this, the API
  returned all of it and `Upload.jsx` discarded it — the app looked identical
  to Phase 1. A third input is added alongside file-drop and URL: a text box
  for typing an achievement directly.
- **GitHub results get their own card.** `GitHubCard.jsx` renders a repo as a
  repo (stars, license, language mix, homepage) and a profile as a profile
  (bio, repo list); `ResultCard.jsx` handles files, written responses, and
  generic web pages. The two share primitives via `cardParts.jsx` rather than
  a layout, so the pieces carrying rules — the category badge, the confidence
  meter, the assumed-date flag — exist once. The raw scraped text moved into a
  collapsed disclosure: it is still the only way to tell "the AI misread this"
  from "the scraper got nothing", but it is no longer what every card ends on.

Category colors live in `frontend/src/categories.js` — one source of truth, so
the Phase 6 timeline and Phase 5 graph color a category the same way an upload
card does. The hues follow plan.md §4 Module 4; the exact steps come from a
validated categorical palette rather than taste, and the file records the
validator results and the two candidate orderings that failed.

### Phase 4 capabilities

- **Semantic search.** `POST /api/search` finds documents by meaning, not
  keywords. Each document's `raw_text` is chunked (~900-char overlapping
  windows, title prepended) and embedded with **all-MiniLM-L6-v2** into
  ChromaDB. Embedding runs locally on CPU, so unlike the Gemini calls it is free
  and **not** rate-limited. The model runs through Chroma's bundled **ONNX**
  export rather than sentence-transformers — same weights, verified to produce
  identical vectors, but 212 MB resident instead of torch's 439 MB, which is
  what makes it fit the 512 MB deploy target (see [Deployment](#deployment)).
- **Instant filters, semantic fallback.** A deterministic router
  (`ai/query_router.py`) answers "show all my certificates" or "my latest
  resume" straight from SQLite — no embedding, no Gemini, no latency — and sends
  only genuine question-shaped queries ("how does my cert relate to my
  internship?") to vector search. This keeps the search screen fast and reserves
  scarce Gemini quota for the RAG answer card (Phase 7). The plan's Path 3 used
  Gemini to *parse* every query; that shares the categorizer's rate-limiter lane
  and would stall a search issued right after an upload, so query understanding
  is done here deterministically and Gemini is reserved for answer synthesis.
- **Every result links to its original.** A hit is hydrated from SQLite (the
  source of truth) and carries its category, date, and a `has_original` flag — a
  file to download, or the source URL / text for a fileless document. The vector
  store decides relevance; the database decides what exists.
- **SQLite is the source of truth; Chroma is rebuildable.** Embeddings are
  derived, never authoritative. The store syncs to SQLite on startup, fills a
  partial index incrementally, and a deleted or corrupt `data/chroma/` is fully
  rebuilt from `raw_text` — which was preserved intact for exactly this.
- **Isolation built in.** Vector queries filter by `user_id`, so results cannot
  cross users once multi-user auth lands. Enforced in code and mutation-tested.

### Phase 5 capabilities (backend)

- **Knowledge graph.** `GET /api/graph` returns `{nodes, edges}` for the
  force-directed view. It is built **on read** from SQLite + the vector store —
  at a student-profile scale, recomputing edges is instant and can never go
  stale. Two deterministic layers (no Gemini): **entity edges** connect every
  document to a shared skill node (typed `certifies_skill` for a certificate,
  `skill_used_in` otherwise — one skill hub per distinct value), and
  **similarity edges** (`similar_to`) link documents whose cosine similarity
  exceeds 0.75, reusing the existing semantic query rather than a second vector
  API.
- **Career-path inference.** `POST /api/career-paths` sends the whole profile to
  Gemini and infers likely trajectories — "AI/ML Engineer · 87%" — with the
  supporting documents and the skills still to learn. Triggered explicitly (it
  costs quota and is stable between uploads), persisted to the `career_paths`
  table, and merged into the graph as `career_path` nodes with `leads_to` edges.
  Like the categorizer it **never raises**: a failure returns no paths plus a
  structured reason, and a quota wall on re-inference does not wipe a good set.
- **Structured degradation contract.** Both Gemini callers now degrade through
  `ai/degradation.py`: a failed result carries a `degraded_reason`
  (`quota | timeout | unreachable | no_api_key | unreadable_response | no_text`)
  and a `retryable` flag, surfaced on the API — so the UI can offer "try again"
  for a quota wall but not for a missing key, instead of parsing prose. A
  retryable card's **Try again** button calls `POST /api/documents/{id}/recategorize`,
  which re-runs categorization over the preserved text and updates the row in
  place (the original file is never touched).
- **One shared rate limiter.** The free-tier budget is per-key, not per-module,
  so every Gemini caller queues through a single limiter in `ai/gemini.py`.
- **Isolation.** The graph is scoped to `user_id` at every source, and the scope
  is mutation-tested — breaking the `WHERE user_id` filter leaks a foreign node
  and turns the isolation test red.

### Phase 6 capabilities (UI)

- **One nav, four views.** A lightweight view switch (no router): **Timeline**,
  **Search**, **Upload** are live; **Graph** is shown disabled ("soon") until
  Phase 5's UI lands.
- **Timeline (View 2).** The persistent "all documents" view, reading
  `GET /api/documents` (dates already resolved server-side). Grouped by year,
  newest↔oldest toggle, category filter chips, expand-to-detail with skills and
  a download/open action. Sorted on `effective_date` **only**, never the raw
  `extracted_date`; an assumed (upload-date fallback) date is flagged with a
  non-color encoding — a hollow ring dot plus a "date assumed" tag — so it does
  not silently read as a document from today.
- **Search (View 4).** Wired to `POST /api/search`. Filter queries ("show all my
  certificates") return a result grid; question queries return sources ranked by
  relevance. There is **no synthesized answer card yet** — that is Phase 7's RAG
  pipeline, so a question returns ranked sources with nothing faked. Each row
  branches on `has_original` (download original vs open source) and carries a
  format badge.
- **Live processing feedback.** The upload drop zone now shows a pending
  skeleton card per in-flight item, a per-input busy state (uploading files no
  longer disables the URL/text inputs), and an "n of m" batch count — no fake
  percentage bar, since the wait is the Gemini round trip, not bytes.
- **Consistent category color.** The timeline dots, filter chips, and search
  icons all reuse the validated palette in `frontend/src/categories.js`. The
  Career Path graph node is the one type with no category behind it; the palette
  validator was run and **no seventh categorical hue passes** (the six saturate
  the usable space on white), so it is encoded compositely instead — a reserved
  dark slate plus larger size, placement, and a mandatory label
  (`CAREER_PATH_COLOR`).

### Phase 7 capabilities (RAG answer card)

- **Synthesized answers, grounded in your own documents.** A question-shaped
  query ("how does my Python cert connect to my internship?") now returns a
  Gemini-written answer above the sources, not just a ranked list. `/api/search`
  stays instant and paints the sources immediately; a separate `POST /api/answer`
  then synthesizes over **exactly the documents search returned** — no second
  vector query, so the answer can only cite what the user can see.
- **Grounding over fluency.** The prompt forbids anything outside the provided
  sources and is told to say so when they don't cover the question; citation
  indices outside the given set are dropped. The card marks which source rows
  the answer actually cited (a "cited" badge/ring), so a reviewer can check the
  answer against its evidence.
- **A third Gemini caller, same contracts.** `ai/rag.py::synthesize` reuses the
  one shared rate limiter and the item-B degradation contract as-is — like the
  categorizer and career-path inference it **never raises and never fabricates**:
  any failure returns no answer plus a structured `degraded_reason`, and the UI
  degrades to sources-only rather than inventing an answer on a quota wall.

### Phase 8 capabilities (demo seed)

- **"Load Demo Profile" button.** The empty timeline and empty graph each carry a
  one-click CTA (plan.md §6: "empty states seed the demo") that populates a
  realistic 10-document student journey — a 2023 Python certificate through a
  2026 resume and portfolio — so a reviewer arriving at an empty app sees the
  graph, timeline, and search working on real material immediately.
- **Backed by `POST /api/seed-demo`** and the runnable
  `seed/seed_demo.py` (`PYTHONPATH=backend python -m seed.seed_demo`). Both call
  the same loader, which inserts directly through `database.insert_document` +
  `embeddings.add_document` with **no Gemini call** — categories and skills are
  hand-authored — so it is fast and costs no quota.
- **Tuned so the graph is impressive.** Skills are authored so a **Python skill
  hub** wires the cert → project → internship → resume chain plan.md §3 names,
  and every document is written at full length so Layer-B `similar_to` edges
  (cosine > 0.75) actually form — four of them, on the real embedding model.
  (Career-path nodes still come from the graph's "Infer career paths" button, a
  real Gemini call; the dataset is tuned so inference lands on AI/ML Engineer.)
- **Idempotent and non-destructive.** Demo documents use deterministic `demo-*`
  ids; re-loading replaces the prior demo set rather than duplicating it, and the
  clear is scoped to `demo-*` — a reviewer's own uploads survive a re-seed. Every
  demo document is fileless (`url` / `text_entry`, no original file).

### Phase 9 capabilities (in progress — UI polish + edge cases)

- **Document delete.** `DELETE /api/documents/{id}` removes a document from every
  store it lives in — its SQLite row plus entity/tag rows, its vector chunks, and,
  for an uploaded file, the original and its `.meta.json` sidecar. Deleting a whole
  document at the user's request is a *removal*, not the forbidden in-place
  *modification* of a preserved original. The authoritative SQLite row goes first
  and the derived stores are cleaned best-effort, so a hiccup leaves a harmless
  orphan, never a record that outlives its document. Scoped to `user_id` (the same
  isolation boundary the graph enforces) and mutation-tested. In the UI, each
  timeline entry gains a Delete action behind a two-step inline confirm.
- **Explicit "Ask AI" on search.** Query *understanding* stays deterministic and
  Gemini is reserved for answer *synthesis*, which auto-fires only for
  question-shaped queries. A filter or plain semantic search would otherwise never
  offer an AI answer, so an **Ask AI about these results** button synthesizes one
  on demand — grounded in exactly the sources shown — without touching the
  instant-search path.
- **Manual category override.** `PATCH /api/documents/{id}/category` lets the user
  overrule Gemini from the timeline entry, completing plan.md § Risk Mitigation's
  "allow manual override". The six-category taxonomy cannot fit everything — a
  whole GitHub *profile* is not a project, a skill, or a certification, and lands
  in *Projects* — so rather than fight the model, the user gets the last word.
  Deliberately narrow: it relabels, and nothing else moves. Not the original, not
  the extracted text, not the entities, and not `confidence`, which reports on the
  *model's* classification and would be a lie about a category the model did not
  choose. The choice is recorded as `category_source: manual` — surfaced on every
  document so nothing presents a user's correction as the AI's judgment, and
  honoured by `/recategorize`, which would otherwise quietly undo the override the
  next time a degraded card was retried. No re-indexing: the graph types its skill
  edges from `category` and computes them on read, so a certificate reclassified
  as a project stops emitting `certifies_skill` edges by itself, and the vector
  store never embedded the category at all.
- **Search finds documents the model filed elsewhere.** Found by testing with a
  real résumé. The router maps a typed word to a category ("resume" → *Academics*),
  but the category is Gemini's judgment: it filed an actual résumé under *Skills*,
  and the SQL filter then excluded the one document the query named. The category
  was a guess at what the model *should* have chosen while the answer was already
  stored — that document's `document_type` is literally `resume`. A filter keyword
  now carries **both**, and a document matching *either* is a hit. The same fault
  hid the seed's *Hackathon Winner Certificate* (`document_type=certificate`,
  category *Achievements*) from "show all my certificates", a plan.md §16
  must-work query.
- **An empty filter falls back to semantic search.** The word→category guess is
  the weakest link in the retrieval chain, and when it misses, the documents are
  still there and still embedded — an empty page tells the user the opposite. A
  filter that matches nothing is re-run as a semantic search, and the response
  says so (`fell_back`) so the UI can mark the rows as *closest matches* rather
  than passing related results off as the exact set the query named.
- **Gemini Vision OCR — scans stop arriving empty.** plan.md promises this
  fallback three times (§2, §4 Module 1, § Risk Mitigation) and it did not exist.
  Local OCR needs **Tesseract and Poppler**, external binaries that are not on
  this dev machine and cannot be installed on Render's free native-Python runtime
  — so on both, a scanned certificate was stored with `raw_text = ""` while the
  upload reported success: the categorizer fell back to a filename guess, the
  embedding carried no signal, and the document was unfindable. Silent, and in
  the one area the project is judged most on (§15, retrieval = 40%).
  `ai/vision.py` is a fourth Gemini caller under the same three contracts as the
  others — the one shared rate limiter, redacted logs, and **never raises**.
  `ocr_handler` is now a two-rung ladder: local OCR first (free, no quota), Gemini
  Vision only when it yields nothing. A PDF goes to the API **whole**, which
  rasterizes pages server-side, so one call replaces *both* missing binaries.
  The model is asked for a verbatim transcript, never a description — a plausible
  description of a document nobody read would be embedded and cited as if it had
  been — with an explicit sentinel for "nothing legible", so the model's own prose
  about failing is never stored as the document's text.
- **A failed extraction now says which rung failed.** The old warning read
  "OCR produced no text (Tesseract unavailable or blank image)" — one sentence for
  two unrelated causes with opposite fixes. A missing binary is the operator's
  problem, a quota wall clears itself, a blank scan is nobody's; each is now
  reported distinctly, along with the structured reason and its `retryable` flag.
- **The free tier is 5 RPM and 20 requests per _day_ — not 10 RPM / 1500 RPD.**
  Found by this work: the second Gemini call per scanned upload pushed the live
  suite into 429s that named both real ceilings —
  `GenerateRequestsPerMinutePerProjectPerModel-FreeTier` → `limit: 5`, and
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` → **`limit: 20`**, model
  `gemini-3-flash`. The docs no longer publish per-model free-tier limits (they
  defer to AI Studio), so an enforced quota in a live 429 is the best evidence
  available — and better than a doc table, being this key's actual limit.
  - **RPM is handled:** the shared limiter had been spacing calls 6.5s apart,
    about 9 RPM and nearly double the real budget, so any two callers in quick
    succession were already gambling. Now 13s.
  - **RPD is not, and cannot be by spacing.** 20/day is the binding constraint on
    the whole project: a full `pytest -m live` run is 8 calls (40% of a day), a
    scanned upload is 2, and the plan.md §10 demo script's "upload 8-10
    documents" would spend a day's quota by itself. This is what makes Phase 8's
    **"Load Demo Profile" — which issues no Gemini call at all** — load-bearing
    rather than a convenience, and it is why every caller degrades instead of
    failing. plan.md §11's cache/batch/queue mitigations remain unbuilt.
  - **Known cost:** a scanned upload makes two calls, so ~26s at 13s spacing.
- **Frontend test suite.** The React app now has 142 vitest + Testing Library
  tests where it had none (see [Frontend](#frontend)).
- **A warm theme, on real type.** The app left the default Tailwind indigo and
  slate behind for warm paper surfaces (`parchment` page, `paper` card), an
  **espresso** accent, and a warm-neutral `sand` ink scale — all defined in
  `frontend/tailwind.config.js`. Two things make this more than a repaint:
  - **Every step was solved to match the WCAG contrast of the step it
    replaced**, so hue moved without changing how heavy any text or border
    reads. The accent is deliberately *achromatic-warm*: it sits beside category
    badges constantly, and a brown that reads as chrome can never be mistaken
    for a category the way a chromatic accent could — the same reasoning
    `CAREER_PATH_COLOR` already used.
  - **The six category hues were not touched.** The palette validator was re-run
    against each candidate warm surface before anything changed: all six pass,
    both existing WARNs stay conditional (and their relief condition — the
    category *name* always rendering as text beside the dot — is unchanged), and
    nothing crosses into FAIL.

  Type is **Fraunces** (display) + **Inter Tight** (UI), bundled by Vite via
  `@fontsource-variable` rather than linked from Google's CDN, so the deployed
  app makes no third-party request and cannot lose its type if that CDN is
  blocked. The serif is applied to exactly three places — the wordmark, document
  titles, and timeline years — with **no blanket `h1–h3` rule**, because half
  this app's headings are small utility labels where a display serif at 14px
  reads as a mistake.

  One fix rode along: `text-slate-400`, the app's most-used text class (37 uses,
  all real text), was **already below the 4.5:1 AA floor** at 2.56:1 on white and
  would have gone to 2.38 on beige. It is now `sand-500`, 5.29:1 on paper and
  4.71 on parchment. The cost is that it merges with the old `slate-500`, so the
  two quietest text tiers are now one.
- **The free tier is stated up front.** A standing `QuotaNotice` under every view
  says the app runs on Gemini's free tier at **5 requests per minute and 20 per
  day**, that AI features degrade rather than fail once that is spent, and that
  loading the demo profile costs no AI calls. One honest disclosure replaced the
  per-action warnings (the career-path button's "· costs quota"): a reviewer
  needs the ceiling once, up front, not a reminder at every click. The numbers
  are mutation-tested — restoring the old, wrong "1500 per day" reddens the
  assertion — because the entire point of the box is that its figures are true.
- **A failed extraction is no longer terminal** (`POST /api/documents/{id}/reextract`).
  Extraction sits upstream of everything, so a scan that lost its Vision call to
  the daily quota was stored with empty `raw_text` — unfindable, wearing a
  filename guess for metadata, and beyond repair: `/recategorize`, the existing
  retry, re-runs the *model* over that same empty text and changes nothing. The
  only cure was delete-and-reupload, which discards the upload date and the id.
  Rare at the 1500/day this repo once assumed; **normal at 20/day.**

  The fix is the preservation guarantee paying off — the original was stored
  byte-for-byte and is only ever read, so the pixels are still there to try
  again. The route re-reads them and rewrites only what was derived: `raw_text`,
  the sidecar's extraction block, and the vectors. It verifies the checksum
  first, exactly as the download path does, rather than deriving new text from a
  file that no longer matches its integrity record.

  It is quota-aware because it exists for a quota problem: a run that recovers
  nothing spends **no** further call (classifying an empty string buys nothing),
  keeps the text already stored rather than overwriting it with `""`, and
  returns 200 with *this* attempt's reason — a retry that fails is an honest
  "not yet", not an error. A run that recovers text classifies it, because text
  recovered into a document still labelled by filename is the state being
  repaired. Both rules are mutation-tested: writing the empty result reddens the
  don't-destroy-text assertion, and forcing the re-classification reddens the
  no-wasted-call one.
- **Extraction degradation is structured, not prose.** The reason a document has
  no text was only ever a sentence in `warnings`, while categorization had
  carried `degraded_reason` + `retryable` since deferred item B. Extraction now
  reports the same contract (`ExtractionResult.degraded`, surfaced as
  `extraction_degraded_reason` / `extraction_retryable` and persisted to the
  row), so a client can tell a quota wall that clears itself from a missing
  Tesseract binary that never will — which is exactly the judgment the retry
  button above needs to make. The invariant tying the two together: the reason is
  set **exactly** when the no-text warning is emitted, so prose and code cannot
  disagree about whether extraction failed.
- **Each visitor gets their own dataset.** Found by deploying: every route
  pinned a single `DEFAULT_USER`, so the public URL served **one shared
  library**. The first visitor's "Load Demo Profile" click populated the app for
  everyone arriving after — and, the part that actually matters, anything a
  visitor uploaded was readable, downloadable, and deletable by the next one. A
  reviewer trying it with their real résumé published it to strangers.

  The storage layer had been user-scoped from the start (`list_documents`,
  `delete_document`, `build_graph`, `embeddings.query`, `uploads/{user_id}/`);
  what was missing was any *identity* to scope by. The frontend now mints a uuid
  into `localStorage` and sends it on every request (`X-User-Id`), and
  `backend/identity.py` resolves it into the `user_id` every route already knew
  how to use.

  Three things were not free:
  - **`career_paths` had no `user_id` column at all**, so inferred paths would
    have leaked across visitors while documents stayed private — and the
    unscoped `DELETE` meant one visitor re-inferring wiped everyone's. Column
    added, with an idempotent startup migration, because `CREATE TABLE IF NOT
    EXISTS` never alters a table that already exists.
  - **`/answer` takes document ids straight from the client**, which made it the
    easiest read-across in the app: post someone else's ids and let Gemini
    summarize the contents back. Each id is now re-checked against the caller.
  - **The download link is an `<a href>`**, and a browser navigation cannot
    carry a custom header, so the id rides as `?u=` there. The header wins when
    both are present.

  **This is separation, not authentication.** The id is client-generated and
  sits in `localStorage`; anyone can send someone else's. It stops two reviewers
  colliding, not a determined one — real auth is plan.md §17's stretch goal.
  Mutation-tested: removing the id validation, either ownership check, or the
  scoping on the career-path delete each reddens exactly the test that names it.
- **Next:** the rest of the real-document edge-case pass (an awkward PDF, a
  dead or private-IP URL, an empty entry, responsive layout).

### Original Format Preservation

Treated as a hard guarantee, enforced in code and covered by tests:

- Originals are written **byte-for-byte unchanged** to `uploads/{user_id}/`.
- A **SHA-256 checksum** is computed at upload and re-verified against what
  landed on disk — a bad write fails loudly instead of corrupting silently.
- Extracted text and metadata live in a **separate `.meta.json` sidecar**; the
  original is only ever read after being written.
- Downloads **re-verify the checksum** and refuse to serve a file that fails.
  As of Phase 2 the same metadata is also indexed in SQLite; the sidecar is kept
  as the on-disk source of truth so integrity does not depend on the database.

---

## Setup

### Backend (Python 3.12)

```bash
cd backend
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API docs at http://localhost:8000/docs

> **OCR (optional):** local OCR of scans and images uses the external
> **Tesseract** and **Poppler** binaries. They are genuinely optional now —
> without them extraction falls through to **Gemini Vision**, which needs no
> local binaries and reads PDF pages directly. Installing them only saves a
> Gemini call per scanned upload. Set `VISION_OCR_ENABLED=false` to turn the
> fallback off and rely on local OCR alone.

### Frontend (Node 18+)

```bash
cd frontend
npm install
npm run dev
```

App at http://localhost:5173 (proxies `/api` to the backend on :8000).

### Config

Copy `.env.example` to a `.env` in the **project root** (not `backend/`) and fill in
your key — `backend/config.py` reads the root `.env` and maps each variable onto a
setting automatically.

```bash
cp .env.example .env
```

| Variable         | Required        | Notes                                            |
| ---------------- | --------------- | ------------------------------------------------ |
| `GEMINI_API_KEY` | Phase 2 onward  | From [Google AI Studio](https://aistudio.google.com/apikey) |
| `GEMINI_MODEL`   | no              | Defaults to `gemini-3-flash-preview`             |
| `DEBUG`          | no              | `true` enables verbose logging                   |
| `VISION_OCR_ENABLED` | no          | Default `true`. Read scans with Gemini Vision when local OCR finds nothing. Off = local OCR only, so no text at all without Tesseract |
| `CORS_ORIGINS`   | deploy only     | Comma-separated browser origins allowed to call the API. The Vite dev origins are always allowed on top |
| `VITE_API_URL`   | deploy only     | **Frontend** build-time var (`frontend/.env` or the Vercel dashboard). Where the API lives; empty locally so the Vite proxy handles it |

`.env` is gitignored — never commit it. For deployment (Phase 10), set these as
environment variables in the host dashboard instead of shipping the file. If a key
is ever exposed, rotate it in AI Studio rather than trying to scrub it.

Verify your config loaded (prints no secrets):

```bash
cd backend
.venv/Scripts/python.exe -c "from config import settings; print('key set:', bool(settings.gemini_api_key), '| model:', settings.gemini_model)"
```

---

## Deployment

Two free services, both live:

| | service | URL |
|---|---|---|
| Frontend (Vercel) | `trace-ai` | **https://trace-ai-eta.vercel.app** — the submitted URL |
| Backend (Render) | `traceai-api` | https://traceai-api-flmc.onrender.com |

**Neither name came out as planned, and that matters more than it looks.** Both
hosts append a suffix when the name you ask for is taken, so the real origins
are not guessable from the service names — and `CORS_ORIGINS` below has to hold
the frontend's *actual* origin, not the one the plan assumed.

Both services are described by committed config — `vercel.json` and
`render.yaml` — so the dashboards mostly just point at this repo. `vercel.json` carries no comments because JSON allows none; what
it does is set the install/build commands and `frontend/dist` as the output. It
adds no SPA rewrite on purpose: the app is a view switch with no router, so
there are no client-side routes to rewrite.

### Steps

**Render — `traceai-api`:**

1. **New → Blueprint**, connect this repo. Render reads `render.yaml`.
2. Set **`GEMINI_API_KEY`** in the dashboard. It is `sync: false` in the
   blueprint precisely so it is never committed.
3. Deploy. The first build is slow — it installs dependencies and warms the
   ~79 MB ONNX embedding model into the image so no user's first upload pays
   for the download.
4. **Note the URL Render actually assigned** — it is not necessarily
   `traceai-api.onrender.com` (ours became `traceai-api-flmc.onrender.com`).
   Check `<that-url>/api/health` returns
   `{"status": "ok", "ai_configured": true, ...}`. `ai_configured: false` means
   the key did not reach the service.

**Vercel — `traceai`:**

1. **New Project**, import this repo. Vercel reads `vercel.json`.
2. Set **`VITE_API_URL`** to the Render origin from step 4 above, no trailing
   slash (`https://traceai-api-flmc.onrender.com`). It is the **only**
   environment variable the frontend needs — and the Gemini key must never be
   among them: anything `VITE_`-prefixed is inlined into the JavaScript bundle
   and served to every visitor. Vite inlines at **build** time, so this must be
   set *before* the first build; changing it later needs a redeploy, not a
   restart.
3. Deploy, open the site, confirm the timeline loads and "Load Demo Profile"
   populates it.

**Then reconcile `CORS_ORIGINS` on Render with the origin Vercel actually
assigned.** This is the step that bit us: the frontend landed on
`https://trace-ai-eta.vercel.app`, not the `https://traceai.vercel.app` the
blueprint assumed, so every API call was blocked. It presents as a UI that
loads and then does nothing — "failed to load demo" — with CORS errors in the
browser console, while `/api/health` still returns `ok` and both dashboards
show a green deploy. Nothing looks broken from either end.

The check that identifies it in one shot, without a browser: send a preflight
and see whether the header comes back at all.

```bash
curl -si -X OPTIONS https://traceai-api-flmc.onrender.com/api/seed-demo \
  -H "Origin: https://trace-ai-eta.vercel.app" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control-allow-origin
```

An allowed origin echoes back in `Access-Control-Allow-Origin`; a rejected one
returns 400 with the header absent. Preview deploys get their own `*.vercel.app`
origins and are blocked the same way until added.

### Free-tier constraints — know these before demoing

- **No persistent disk.** `uploads/`, `data/traceai.db` and `data/chroma/` are
  wiped on every deploy and every restart. Most of this self-heals: the vector
  store rebuilds from SQLite, and "Load Demo Profile" re-seeds with no Gemini
  call. **An uploaded original does not come back** — its `download` link 404s
  afterwards, which is the one place the § Original Format Preservation
  guarantee is limited by the host rather than by the code. The guarantee holds
  for the lifetime of the instance; a reviewer returning days later sees the
  demo profile, not their upload. A persistent disk is a paid feature.
- **~15-minute spin-down.** The first request after an idle period waits for a
  cold start. Deliberately **not** worked around with a keep-warm ping: the free
  allowance is 750 instance-hours/month, which barely covers a single always-on
  service, so pinging would spend the month's budget to save one wait.
- **512 MB RAM.** The reason embeddings run on ONNX rather than torch. If you
  reintroduce `sentence-transformers`, the service will OOM — re-measure before
  changing anything under `ai/embeddings.py`.
- **No Tesseract or Poppler**, and they cannot be installed on the native
  Python runtime. Scans therefore go straight to the Gemini Vision rung, which
  costs one extra call from the **20/day** budget per scanned upload.

---

## API

| Method | Endpoint                        | Description                                             |
| ------ | ------------------------------- | ------------------------------------------------------- |
| GET    | `/api/health`                   | Health check; reports whether an API key is configured   |
| POST   | `/api/upload`                   | Multipart upload → extracted text + sha256 + categorization |
| POST   | `/api/ingest-url`               | `{ "url": "..." }` → scraped text + categorization, stored; GitHub repos/profiles also return a `details` object |
| POST   | `/api/ingest-text`              | `{ "text": "..." }` → written response, categorized + stored |
| POST   | `/api/search`                   | `{ "query": "...", "k": 5 }` → routed to a SQL filter (matching category **or** document type) or semantic vector search; ranked source documents. `answerable` flags a question-shaped query for the answer card; `fell_back` marks results served semantically after an empty filter |
| POST   | `/api/answer`                   | `{ "query": "...", "doc_ids": [...] }` → Gemini-synthesized RAG answer grounded in those documents, with citations + any degradation |
| POST   | `/api/seed-demo`                | Load the 10-document demo profile (plan.md §14); idempotent, no Gemini call |
| GET    | `/api/graph`                    | `{ nodes, edges }` for the knowledge graph — documents, skill hubs, career paths, and their edges |
| POST   | `/api/career-paths`             | Infer career trajectories over the whole profile (Gemini); persists and returns them + any degradation |
| GET    | `/api/documents`                | List categorized documents; `?category=` filters        |
| GET    | `/api/documents/{id}`           | Full detail — entities, tags, extracted text            |
| POST   | `/api/documents/{id}/recategorize` | Re-run categorization over the preserved text (the retry path); updates the row in place. A manually overridden category is kept |
| POST   | `/api/documents/{id}/reextract` | Re-run **extraction** over the preserved original, then re-classify what it recovers. 409 when there is no original (URL / text entry); a run that recovers nothing keeps the stored text and spends no further quota |
| PATCH  | `/api/documents/{id}/category`  | `{ "category": "..." }` → manually override the AI's category (one of the six); marks it `manual`. User-scoped |
| DELETE | `/api/documents/{id}`           | Delete a document from every store — SQLite, vectors, and the original + sidecar; user-scoped |
| GET    | `/api/documents/{id}/download`  | Original file, integrity-verified                       |
| GET    | `/api/documents/{id}/verify`    | Recompute checksum, report match                        |

### Data storage

| Store                        | Holds                                                   |
| ---------------------------- | ------------------------------------------------------- |
| `uploads/{user_id}/`         | Originals, byte-for-byte unchanged                      |
| `uploads/.../{f}.meta.json`  | Sidecar — on-disk source of truth for integrity         |
| `data/traceai.db`            | SQLite — queryable metadata, entities, tags, inferred career paths |
| `data/chroma/`               | ChromaDB — document embeddings for semantic search (derived; rebuildable from SQLite) |

Graph relationships (document↔skill, `similar_to`) are **computed on read**, not
stored — only the Gemini-inferred `career_paths` are persisted, since they are
the one part expensive to recompute.

The sidecar and the database are written from the same upload, deliberately
duplicating checksum and extraction data: an original plus its sidecar can be
verified even if the database is lost. The vector store is the one exception to
this belt-and-braces rule — it holds nothing that is not regenerable from
`raw_text` in SQLite, so it is treated as a cache, not a source of truth.

---

## Tests

The backend is tested with pytest; the frontend with vitest + Testing Library
(see [Frontend](#frontend) below). Both run offline by default — no network, no
API quota.

```bash
cd backend
pytest              # 417 tests, no network, ~1.5 min
pytest -m network   # 9 more that make real HTTP calls (no API quota, ~7s)
pytest -m live      # 7 more that call the real Gemini API (needs a key, ~2 min)
pytest -m model     # 2 more that load the real embedding model (~80MB download first run, ~10s)
                    # frontend: 142 vitest tests, see Frontend below
```

Tests run against a per-test tmp directory, so they never write to the real
`uploads/`, `data/traceai.db`, or `data/chroma/`. Embeddings are stubbed with
deterministic vectors by default; the `model` tests opt into the real ONNX
MiniLM to check its dimension and that a relevant document actually ranks first.
They go through `embed_texts`, the single choke point, so they exercise whatever
backend is wired in — which is how the torch→ONNX swap was checked.

| File                     | Covers                                                    |
| ------------------------ | --------------------------------------------------------- |
| `test_preservation.py`   | The section-1 guarantee — checksums, byte-exact download, **tamper detection** |
| `test_extraction.py`     | DOCX / PPTX / TXT extraction and upload error paths        |
| `test_categorizer.py`    | Response parsing and normalization of drifted model output |
| `test_documents_api.py`  | Categorization persisted to SQLite and read back           |
| `test_security.py`       | Regression tests for fixed vulnerabilities                 |
| `test_url_guard.py`      | SSRF guards — schemes, private/multicast addresses, redirect hops, size caps |
| `test_ingest_fileless.py`| URL + written-response ingestion reaching SQLite            |
| `test_dates.py`          | Repo creation dates, and the known-vs-assumed date flag    |
| `test_github_ingest.py`  | Repo enrichment, profile scraping, URL routing, and the link-scheme guard |
| `test_embeddings.py`     | Chunking, add/query/delete, multi-chunk dedup, **user_id isolation**, rebuild-from-SQLite |
| `test_search.py`         | Query routing (filter vs semantic), the `/api/search` endpoint, the category-vs-document-type mismatch, and the empty-filter fallback |
| `test_relationship_engine.py` | Entity + similarity edge construction (Module 3 Layers A/B) |
| `test_graph_api.py`      | `/api/graph` nodes/edges, career merge, and **mutation-tested user isolation** |
| `test_career_path.py`    | Career-path inference — index mapping, clamping, never-raises, no-wipe on degrade |
| `test_degradation.py`    | The item B contract — reason→retryable table and exception classification |
| `test_rag.py`            | RAG synthesis — grounding, citation clamping, never-raises/degrades, `/api/answer` |
| `test_seed.py`           | Demo seed — endpoint, idempotency, non-destructive re-seed, the Python skill hub |
| `test_delete.py`         | Document deletion across SQLite, vectors, and the file/sidecar; **user-scoped isolation** |
| `test_category_override.py` | The manual override — the taxonomy guard, relabel-only (original/text/entities untouched), the graph re-forming on read, survival of a re-categorization, **user-scoped isolation** |
| `test_vision.py`         | Gemini Vision OCR across all four layers — the call's guards (config gate, key, inline size cap, mime conversion, the no-text sentinel, never-raises, key redaction), the local-first ladder, the warning that names the failing rung, and a scanned upload landing searchable |
| `test_reextract.py`      | Re-extraction as repair — recovery of text a quota wall lost (re-classified, re-indexed), and the four ways it must not make things worse: never overwrite stored text with an empty result, never spend a call classifying nothing, never derive from an original that failed its checksum, and 409 rather than 404 for a document that legitimately has no original. Also the structured extraction reason (`no_text` vs a retryable `quota`) at the API and in the row |
| `test_identity.py`       | Per-visitor isolation — documents, search, graph, RAG, career paths and the retry routes all scoped; cross-user read/download/delete/override each 404; the id allowlist rejects path traversal; the `?u=` download fallback, and the header beating it. **The only file that sends two distinct ids** — every other test runs as the fallback user and would pass with the isolation removed |
| `test_url_network.py`    | Opt-in; real GitHub API, real redirect chain               |
| `test_live_gemini.py`    | Opt-in; catches a retired model id or revoked key; real career-path + RAG inference, and **real Vision OCR** — the only test that proves the inline-blob format and that the configured model still reads images |

`live` tests are deselected by default because they cost free-tier quota and
need network. They are the only tests that catch a retired model id, a changed
response shape, or an expired key — the stubbed suite passes through all three,
so run them after changing anything in `ai/`.

`network` tests are deselected for the same reason but cost no quota. They are
the only tests that exercise real HTTP: every other URL test stubs `safe_get`,
so nothing else would catch a changed GitHub response shape or a redirect loop
that stopped following hops.

The suite was validated by mutation: removing the doc-id guard, the log
redaction, the checksum comparison, the private-address check, the multicast
exclusion, or the streamed size count each causes the corresponding test to
fail. One finding from that pass is recorded in `test_ingest_fileless.py` — the
route-level SSRF test stays green if you remove *either* validation layer,
because `scrape_url` and `safe_get` both validate. Both are kept: `safe_get`
covers redirect hops that `scrape_url` never sees.

Phase 3's later additions were mutation-validated the same way — breaking the
link-scheme allowlist, the reserved-path denylist, the fork exclusion, the
unknown-user fallthrough, or the assumed-date flag each turns the matching test
red. Two assertions were **hollow** when first written and are worth knowing
about: the scheme-allowlist test was passing only because an unrelated
`netloc` check rejected all its payloads, so it stayed green against a
`javascript:`-only blocklist. It now includes `ftp:` and `gopher:` cases, which
carry a host and can therefore only be rejected by the allowlist itself. A
green run is not evidence; see the mutation-testing rule in `CLAUDE.md`.

Phase 4 added one assertion to that set: the vector store's `user_id` filter.
Dropping `where={"user_id": ...}` in `embeddings.query` makes another user's
document leak into search results and turns `test_query_is_filtered_by_user_id`
red — verified by mutation before the code was committed.

The Vision OCR work was mutation-validated the same way — all ten of its guards.
Removing the config gate, the key check, the inline size cap, the PNG conversion
for a mime the API rejects, the no-text sentinel, the never-raises `except`, the
log redaction, the local-before-Gemini ordering, or either half of the
which-rung-failed warning each turns exactly one test in `test_vision.py` red.

Two traps that pass worth recording, since neither shows up in a green run:

- A stub that **raises** "this must not be called" proves nothing here.
  `extract_text` catches every exception from `_generate` by design, so the
  assertion would be swallowed into a degraded result and the test would pass
  whether the call happened or not. The tests use a **recorder** and assert the
  call list is empty.
- The mutation harness patches source as **bytes**, and this repo mixes line
  endings — pre-existing files are CRLF, newly added ones LF. A `\n`-written
  pattern silently fails to match a CRLF file, which surfaces as "pattern not
  found", indistinguishable at a glance from a guard that moved. Two mutations
  reported that before the harness learned to match the file's own endings.

Phase 5 added a second isolation assertion at the graph layer: breaking the
`WHERE user_id` filter in `database.list_documents` leaks a foreign document
into `GET /api/graph` and turns `test_graph_excludes_other_users_documents` red
— likewise mutation-verified.

### Frontend

```bash
cd frontend
npm test            # 142 tests (vitest run), jsdom, ~50s
npm run test:watch  # same, in watch mode
```

Runs under jsdom with Testing Library. Nothing hits the network: the API layer
(`src/api/client.js`) talks to a stubbed `global.fetch`, mirroring how the
backend suite stubs `safe_get`. Tests are co-located as `*.test.js(x)` beside
the code they cover.

| File                        | Covers                                                   |
| --------------------------- | -------------------------------------------------------- |
| `categories.test.js`        | The palette mapping is total, unknown categories fall to the neutral ink, and no category resolves to the reserved career-path slate |
| `components/cardParts.test.jsx` | `formatMonth` never invents a day; `knownDate` keeps an *assumed* date out of the meta line (the § Risk Mitigation flag); `formatLabel` is total |
| `api/client.test.js`        | The `handle()` error contract — backend `detail`, status fallback, non-JSON body — plus request shapes, incl. multipart upload |
| `components/LoadDemoButton.test.jsx` | The visible-change contract — seed resolves before the refetch, disables in flight, a failed seed shows the error and does not refetch |
| `components/Timeline.test.jsx` | Year grouping, newest/oldest toggle, **undated-last in both directions** (the `effective_date` rule), present-only chips, category filtering |
| `components/KnowledgeGraph.test.jsx` | The graph's pure model helpers — `buildModel` (drops edges with a missing endpoint, dedups neighbours, bidirectional connections), `colorOf` / `radiusOf` / `isDashed` / `edgeId` |
| `components/AnswerCard.test.jsx` | The three honest states — loading / answered + source count / degraded — and **never-fabricate** on a degraded payload |
| `components/ResultCard.test.jsx` | The item-B retry end to end (recategorize in place, stale warning dropped) and the file-vs-fileless download branch |
| `components/SourceRow.test.jsx` | The § Risk Mitigation assumed-date rule at the row, the cited badge, and the download/open-source branch |
| `components/Search.test.jsx` | Filter-vs-question routing, RAG grounded in the returned ids, sources-only degrade, and the explicit **Ask AI** button |
| `components/Upload.test.jsx` | File / URL / text routing and the deferred item-A **per-input independence** (files busy must not disable the URL input) |
| `components/GitHubCard.test.jsx` | The repo vs profile shapes, the repo-list cap disclosure, and the Upload dispatch that selects this card |
| `components/TimelineEntry.test.jsx` | The delete flow — the two-step confirm gate, notify-parent-to-refetch on success, keep-and-error on failure — and the category override (badge moves on the server's answer only, "set by you" never claimed for an AI category, Uncategorized never offered) |
| `components/QuotaNotice.test.jsx` | The free-tier disclosure states **both measured limits** (5 RPM / 20 per day), the degrade-not-fail promise, and that the demo seed costs nothing |
| `api/userId.test.js`        | Per-browser identity — the id persists across calls, matches the backend's allowlist, is replaced when corrupted, survives localStorage throwing, and differs between browsers; **all 13 API calls send `X-User-Id`** (one that forgets it silently reads the shared dataset) and the download href carries `?u=` |

The `cardParts` suite also covers the rule-bearing shared primitives —
`Confidence` (0.0 is the couldn't-classify warning, not an empty meter),
`DegradedNotice` (behaves on the `retryable` flag), and `OriginalAction`.

The frontend suite was validated by mutation the same way as the backend:
flipping Timeline's undated-last comparison reddens the matching grouping test,
and every suite added since was validated the same way (e.g. dropping the
knowledge-graph missing-endpoint guard, suppressing the Ask AI button, or wiring
delete past its confirm each reddens exactly the test that asserts it). The
quota notice was checked the same way: setting its daily limit back to the old,
wrong 1500 reddens the assertion that names both measured limits.

The category override produced two more hollow assertions worth recording, both
green until mutation found them:

- The failed-save test asserted the old category was still *on screen*. The open
  picker renders a chip per category, so it passed even when the badge had
  optimistically moved to a category the server rejected. It now asserts inside
  the badge's own group — a document-wide text query cannot tell a badge from a
  chip that happens to say the same word.
- The backend's "a re-categorization does not revert a manual override" test
  passed with the database guard removed, because the route was *also* applying
  the rule to the value it sent. Two copies of one rule, and the test could not
  see which one it was exercising. The route now reports whatever
  `update_categorization` stored rather than deciding for itself, leaving one
  guard — which the test does redden.
