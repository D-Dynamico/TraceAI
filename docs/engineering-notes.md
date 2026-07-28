# Engineering notes

The reasoning behind the decisions, and the traps found on the way. Split out of
the README, which had grown into a phase-by-phase changelog. The diagrams live in
[architecture.md](architecture.md); product scope in [plan.md](../plan.md); repo
conventions in [CLAUDE.md](../CLAUDE.md); what changed and when in `git log`.

Organised by topic, not by phase — the phase a thing shipped in stops mattering
the moment it ships.

---

## 1. Original format preservation

The brief states this twice, so it is treated as a hard guarantee, enforced in
code and covered by tests:

- Originals are written **byte-for-byte unchanged** to `uploads/{user_id}/`.
- A **SHA-256 checksum** is computed at upload and re-verified against what
  landed on disk — a bad write fails loudly instead of corrupting silently.
- Extracted text and metadata live in a **separate `.meta.json` sidecar**; the
  original is only ever read after being written.
- Downloads **re-verify the checksum** and refuse to serve a file that fails.

The sidecar and the database are written from the same upload and deliberately
duplicate the checksum and extraction data: an original plus its sidecar can be
verified even if the database is lost. The vector store is the one exception —
it holds nothing that is not regenerable from `raw_text`, so it is treated as a
cache, not a source of truth.

**Fileless documents.** URL and text-entry documents have no original, so
`original_path` is `""` (not NULL — the column is NOT NULL, and keeping it that
way means every reader has one code path) and no sidecar is written. Their
`checksum` is the SHA-256 of the *text*: for a written response the text is the
artifact; for a URL it pins which snapshot of a page was ingested.

**Deleting is not modifying.** `DELETE /api/documents/{id}` removes a document
from every store it lives in. Removing a whole document at the user's request is
a *removal*, not the forbidden in-place *modification* of a preserved original.
The authoritative SQLite row goes first and the derived stores are cleaned
best-effort, so a hiccup leaves a harmless orphan, never a record that outlives
its document.

---

## 2. The free tier is 5 RPM and 20 requests per *day*

Not the "10 RPM / 1500 RPD" this project assumed until 2026-07-25. Found by
accident: the second Gemini call per scanned upload pushed the live suite into
429s that named both real ceilings —

```
Quota exceeded for metric:
generativelanguage.googleapis.com/generate_content_free_tier_requests,
limit: 5, model: gemini-3-flash
quota_id: "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
retry_delay { seconds: 14 }
```

— and `GenerateRequestsPerDayPerProjectPerModel-FreeTier` → **`limit: 20`**.
Google no longer publishes per-model free-tier limits (the docs defer to AI
Studio), so an enforced quota in a live 429 is the best evidence available, and
better than a doc table, being this key's *actual* limit.

**RPM is handled.** The shared limiter had been spacing calls 6.5s apart — about
9 RPM, nearly double the real budget — so any two callers in quick succession
were already gambling. It is 13s now.

**RPD is not, and cannot be by spacing.** 20/day is the binding constraint on the
whole project:

- a full `pytest -m live` run is 8 calls — 40% of a day;
- a scanned upload is 2 calls, so ~26s at 13s spacing;
- plan.md §10's demo script ("upload 8-10 documents") would spend a day's quota
  by itself.

Two consequences are load-bearing rather than conveniences: **every Gemini caller
degrades instead of failing**, and **"Load Demo Profile" issues no Gemini call at
all**. plan.md §11's cache/batch/queue mitigations remain unbuilt.

**One shared limiter.** The budget is per *key*, not per module, so all four
callers (`categorizer`, `vision`, `career_path`, `rag`) queue through a single
instance in `ai/gemini.py`. Four separate limiters would let them issue ~4x the
intended rate. The lock is held across the sleep, which serializes callers —
correct for a fixed RPM budget, where parallelism buys nothing.

Anything logging an exception from the Gemini SDK passes through `redact()`: on
the REST transport those messages can carry `?key=<api key>`, and logs get
copied into issues and CI output.

---

## 3. Ingestion

### The OCR ladder

`ocr_handler` is a two-rung ladder: **local Tesseract first, Gemini Vision
second**. Ordering is deliberate and mutation-tested — local OCR is free and
cannot exhaust a quota, so the rung that spends quota only runs once the free one
has produced nothing.

Rung 2 is not a nicety. Tesseract and Poppler are **external binaries**, absent
from the dev machine and not installable on Render's free native-Python runtime.
On both, rung 1 always yields nothing — so before Vision existed, a scanned
certificate was stored with `raw_text = ""` while the upload reported success:
the categorizer fell back to a filename guess, the embedding carried no signal,
and the document was unfindable. Silent, and in the area the project is judged
most on (retrieval, 40%).

A PDF goes to the API **whole**; the API rasterizes pages server-side, so one
call replaces *both* missing binaries. The model is asked for a verbatim
transcript, never a description — a plausible description of a document nobody
read would be embedded and cited as if it had been — with an explicit sentinel
for "nothing legible", so the model's own prose about failing is never stored as
the document's text.

### SSRF protection

User-supplied URLs are validated before every request: http/https only, and the
hostname must resolve exclusively to publicly routable addresses. Redirects are
followed **manually** so each hop is re-validated (`requests`' own redirect
following would defeat the check), and bodies are capped at 5 MB. Without this,
`http://169.254.169.254/latest/meta-data/` would be fetchable — and its body
returned to the caller — the moment the app was deployed.

`safe_get` returns a `SafeResponse`, **not** a `requests.Response`: enforcing the
size cap consumes the stream, which leaves the real object's `.text`/`.content`
unusable.

The layering is on purpose — `url_scraper` routes, `github_scraper` /
`web_scraper` fetch, `url_guard` decides what may be fetched. `ScrapeResult`
lives in its own module because `url_scraper` imports the scrapers and they need
the type; defining it in `url_scraper` would be an import cycle.

### GitHub

A repo URL pulls description, topics, README, a language breakdown by bytes,
stars, forks, license, creation date and last-push date. A bare profile URL
reaches the user API and returns bio, public repo count and repo list — **one
profile is one document**, the same contract as every other input. github.com's
own routes (`/pricing`, `/explore`, …) are excluded by name, and an unrecognised
single path segment degrades to the generic web scraper rather than storing an
empty profile.

The REST API is called **directly rather than via PyGithub**, which issues its
own HTTP and would bypass `url_guard`.

### Dates

**Never read `extracted_date` directly for display or sorting.** Use
`effective_date` + `date_source` from `database._resolve_date`, the single place
that applies plan.md § Risk Mitigation's upload-date fallback. A NULL
`extracted_date` means "unknown", and reading the column raw either drops the
document or silently dates it to its upload — which is how a repo created in 2011
lands on the timeline today. `date_source` is `extracted` or `assumed`, and the
assumed case must be **flagged, not just filled**: the timeline uses a hollow
ring dot plus a "date assumed" tag, a non-colour encoding.

A source's own metadata (a repo's creation date) is still *known*, so it goes
into `extracted_date` rather than being left NULL for the fallback to invent.

---

## 4. Categorization and the degradation contract

Every upload is classified into a document type, category, title, date, summary,
skills, organizations, people and tags, with a confidence score.

**`categorize()` must never raise.** A missing API key, a rate limit, a timeout,
or unparseable model output all degrade to a filename-based guess with
`confidence = 0.0` and a review warning, rather than failing the request. Model
output is normalized before storage, so a drifted category or a confidence
returned as `85` instead of `0.85` does not corrupt the database.
`vision.extract_text()` must never raise either, for the same reason —
extraction is upstream of everything, so a hiccup there would lose the upload
outright rather than degrade it.

**Degradation is structured, not prose.** A degraded result carries a
`degraded_reason` (`quota | timeout | unreachable | no_api_key |
unreadable_response | no_text`) and a `retryable` flag, surfaced on the API — so
the UI can offer "try again" for a quota wall but not for a missing key, instead
of pattern-matching summary prose.

Extraction reports the same contract one layer upstream
(`ExtractionResult.degraded` → `extraction_degraded_reason` /
`extraction_retryable`, persisted to the row). The two are named with a prefix
because they coexist on one response and mean different things: a document can
extract cleanly and fail to classify, or vice versa. The invariant tying prose to
code: **the reason is set exactly when the no-text warning is emitted.**

The old warning read "OCR produced no text (Tesseract unavailable or blank
image)" — one sentence for two unrelated causes with opposite fixes. A missing
binary is the operator's problem, a quota wall clears itself, a blank scan is
nobody's. Each is reported distinctly now.

### Manual override

`PATCH /api/documents/{id}/category` lets the user overrule Gemini from the
timeline entry. The six-category taxonomy cannot fit everything — a whole GitHub
*profile* is not a project, a skill, or a certification, and lands in *Projects* —
so rather than fight the model, the user gets the last word.

Deliberately narrow: it relabels, and nothing else moves. Not the original, not
the extracted text, not the entities, and not `confidence`, which reports on the
*model's* classification and would be a lie about a category the model did not
choose. Recorded as `category_source: manual`, so nothing presents a user's
correction as the AI's judgment and `/recategorize` does not quietly undo it.

No re-indexing needed: the graph types its skill edges from `category` and
computes them on read, so a certificate reclassified as a project stops emitting
`certifies_skill` edges by itself, and the vector store never embedded the
category at all.

---

## 5. Recovery — `/recategorize` and `/reextract`

**`/recategorize`** re-runs the model over the preserved `raw_text` and updates
the row in place. Offered by the UI only on a *retryable* degradation.

**`/reextract` closes the gap that made extraction failure terminal.**
`/recategorize` re-runs the model over `raw_text`, which is empty exactly when
Vision hit the wall — so it re-classified nothing, and the only cure was
delete-and-reupload, losing the upload date and the document id. Rare at the
1500/day this repo once assumed; **normal at 20/day.**

The fix is the preservation guarantee paying off: the original was stored
byte-for-byte and is only ever read, so the pixels are still there to try again.
The route re-reads them and rewrites only what was derived — `raw_text`, the
sidecar's extraction block, the vectors. It verifies the checksum first, exactly
as the download path does, rather than deriving new text from a file that no
longer matches its integrity record.

Quota-aware, because it exists for a quota problem:

- a run that recovers nothing spends **no** further call (classifying an empty
  string buys nothing), keeps the text already stored rather than overwriting it
  with `""`, and returns **200** with *this* attempt's reason — a retry that
  fails is an honest "not yet", not an error;
- a run that recovers text classifies it, because text recovered into a document
  still wearing a filename guess is the broken state being repaired;
- a run whose text is unchanged and whose categorization was not degraded skips
  the call entirely.

**409, not 404**, for a document with no original: a URL or text entry exists and
is fine, it simply has nothing to re-extract from. Re-fetching a URL is a
*different* operation — the stored checksum pins which snapshot was ingested, so
silently replacing it with today's page would break that guarantee.

Both rules are mutation-tested: writing the empty result reddens the
don't-destroy-text assertion, and forcing the re-classification reddens the
no-wasted-call one.

---

## 6. Search and retrieval

### Why query understanding is deterministic

plan.md's Path 3 used Gemini to *parse* every query. That shares the
categorizer's rate-limiter lane, so a search issued right after an upload would
stall behind the ingest queue — on the one screen that must feel instant — and it
spends daily quota on "show all my certificates". So query *understanding* is
deterministic (`ai/query_router.py`) and **Gemini is reserved for answer
*synthesis*.**

### The résumé bug

Found by testing with a real résumé, and the seed data could never have shown it.
The router maps a typed word to a category ("resume" → *Academics*), but the
category is Gemini's judgment: it had filed an actual résumé under *Skills*, and
the SQL filter then excluded the one document the query named.

The category was a guess at what the model *should* have chosen while the answer
was already stored — that document's `document_type` is literally `resume`. A
filter keyword now carries **both**, and a document matching *either* is a hit.
The same fault hid the seed's *Hackathon Winner Certificate*
(`document_type=certificate`, category *Achievements*) from "show all my
certificates", a plan.md §16 must-work query.

### Empty filters fall back

The word→category guess is the weakest link in the retrieval chain, and when it
misses, the documents are still there and still embedded — an empty page tells
the user the opposite. A filter matching nothing is re-run as a semantic search,
and the response says so (`fell_back`) so the UI marks the rows as *closest
matches* rather than passing related results off as the exact set the query
named.

### Embeddings

`raw_text` is chunked into ~900-char overlapping windows with the title
prepended, and embedded with **all-MiniLM-L6-v2** into ChromaDB. Embedding runs
locally on CPU, so unlike the Gemini calls it is free and **not** rate-limited.

The model runs through Chroma's bundled **ONNX** export rather than
sentence-transformers — same weights, verified to produce identical vectors, but
**212 MB resident instead of torch's 439 MB**, which is what makes it fit the
512 MB deploy target. If you reintroduce `sentence-transformers`, the service
will OOM; re-measure before changing anything under `ai/embeddings.py`.

**SQLite is the source of truth; Chroma is rebuildable.** The store syncs to
SQLite on startup, fills a partial index incrementally, and a deleted or corrupt
`data/chroma/` is fully rebuilt from `raw_text` — which was preserved intact for
exactly this. A hit whose document is gone from SQLite is dropped: the vector
store decides relevance; the database decides what exists.

### RAG

`/search` stays instant and paints the sources immediately; a separate
`POST /api/answer` then synthesizes over **exactly the documents search
returned** — no second vector query, so the answer can only cite what the user
can see.

Grounding over fluency: the prompt forbids anything outside the provided sources
and is told to say so when they don't cover the question; citation indices
outside the given set are dropped. The card marks which source rows the answer
actually cited, so a reviewer can check the answer against its evidence. On any
failure the UI degrades to sources-only rather than inventing an answer.

Synthesis auto-fires only for question-shaped queries, so a filter or plain
semantic search would never offer an AI answer — hence the explicit **Ask AI
about these results** button, which synthesizes on demand without touching the
instant-search path.

---

## 7. Knowledge graph and career paths

`GET /api/graph` returns `{nodes, edges}` built **on read** from SQLite + the
vector store. At a student-profile scale, recomputing edges is instant and can
never go stale. Two deterministic layers, no Gemini:

- **entity edges** connect every document to a shared skill node (typed
  `certifies_skill` for a certificate, `skill_used_in` otherwise — one skill hub
  per distinct value);
- **similarity edges** (`similar_to`) link documents whose cosine similarity
  exceeds 0.75, reusing the existing semantic query rather than a second vector
  API.

**Career-path inference** (`POST /api/career-paths`) is the one Gemini part: it
sends the whole profile and infers trajectories — "AI/ML Engineer · 87%" — with
supporting documents and skill gaps. Triggered explicitly (it costs quota and is
stable between uploads), persisted to its own table because it is the one part
expensive to recompute, and merged into the graph as `career_path` nodes with
`leads_to` edges. Like the categorizer it never raises: a failure returns no
paths plus a structured reason, and a quota wall on re-inference does not wipe a
good set. An evidence id that no longer maps to a live document is dropped, so a
`leads_to` edge never dangles.

---

## 8. Per-visitor identity

**The bug, found by deploying:** every route pinned a single `DEFAULT_USER`, so
the public URL served **one shared library**. The first visitor's "Load Demo
Profile" click populated the app for everyone arriving after — and, the part that
actually matters, anything a visitor uploaded was readable, downloadable, and
deletable by the next one. A reviewer trying it with their real résumé published
it to strangers.

It was not a broken isolation check — there was **no identity at all**. The
storage layer had been user-scoped from the start (`list_documents`,
`delete_document`, `build_graph`, `embeddings.query`, `uploads/{user_id}/`);
nothing ever supplied a second identity. The frontend now mints a uuid into
`localStorage` and sends `X-User-Id` on every request; `backend/identity.py`
resolves it into the `user_id` every route already understood.

Three things were not free:

- **`career_paths` had no `user_id` column**, so inferred paths would leak while
  documents stayed private — and the unscoped `DELETE` meant one visitor
  re-inferring wiped everyone's. Column added, plus an idempotent startup
  migration, because `CREATE TABLE IF NOT EXISTS` never alters an existing table
  (local DBs would otherwise differ from a fresh deploy's).
- **`/answer` takes doc ids straight from the client** — the easiest read-across
  in the app: post someone's ids, let Gemini summarize their documents back. Each
  id is re-checked against the caller now, and skipped silently, exactly like an
  id that no longer exists.
- **The download link is an `<a href>`**, which cannot carry a custom header, so
  the id rides as `?u=`. The header wins when both are present.

**Validation is not optional:** `user_id` is interpolated into a filesystem path,
so an id of `../../etc` would escape the uploads directory. The allowlist is
lowercase hex and dashes, 8–64 chars — a uuid4 and nothing else. A malformed id
falls back to the shared dataset rather than 400ing, because rejecting it would
turn a corrupted `localStorage` value into a hard-broken app with no way out but
clearing site data.

**This is separation, not authentication.** The id is client-generated and sits
in `localStorage`; anyone can send someone else's. It stops two reviewers
colliding, not a determined one. Real auth is plan.md §17.

---

## 9. Interface

**Four views, one nav, no router** — a single piece of view state. A student
should never feel lost, and there are no client-side routes to rewrite on deploy.

**Category colours are one source of truth** (`frontend/src/categories.js`), so a
category is the same colour on a timeline dot, a graph node, and a search icon.
The palette came from a validator rather than taste; the file records the
validator results and the two candidate orderings that failed.

**Career Path is the one node type with no category behind it**, and it was
resolved by running the validator rather than by eye: **no seventh categorical
hue passes.** Every plausible candidate (rose, magenta, teal, orange, deep
purple) failed — the six categories saturate the usable hue space. So it is
encoded **compositely**: a reserved achromatic dark, a larger node, right-side
placement, and a mandatory title + match-% label. Identity never rests on that
colour alone.

**The theme is warm paper** (`parchment` page, `paper` card) with an **espresso**
accent and a warm-neutral `sand` ink scale. Two things make it more than a
repaint:

- **Every step was solved to match the WCAG contrast of the step it replaced**,
  so hue moved without changing how heavy any text or border reads. The accent is
  deliberately *achromatic-warm*: it sits beside category badges constantly, and
  a brown that reads as chrome can never be mistaken for a category the way a
  chromatic accent could — the same reasoning `CAREER_PATH_COLOR` already used.
- **The six category hues were not touched.** The validator was re-run against
  each candidate warm surface *before* anything changed: all six pass, both
  existing WARNs stay conditional, and nothing crosses into FAIL.

One fix rode along: `text-slate-400`, the app's most-used text class (37 uses,
all real text), was **already below the 4.5:1 AA floor** at 2.56:1 on white and
would have gone to 2.38 on beige. It is now `sand-500` — 5.29:1 on paper, 4.71 on
parchment. The cost is that it merges with the old `slate-500`, so the two
quietest text tiers are now one.

Type is **Fraunces** (display) + **Inter Tight** (UI), bundled by Vite via
`@fontsource-variable` rather than linked from Google's CDN, so the deployed app
makes no third-party request and cannot lose its type if that CDN is blocked. The
serif is applied to exactly three places — the wordmark, document titles, and
timeline years — with **no blanket `h1–h3` rule**, because half this app's
headings are small utility labels where a display serif at 14px reads as a
mistake.

**The free tier is disclosed once, not per click.** A standing `QuotaNotice`
under every view states the real ceiling and the degrade-not-fail promise, which
replaced the per-action "costs quota" warnings. A reviewer needs the ceiling up
front, not a reminder at every button.

**Live pipeline feedback** on upload: a pending skeleton card per in-flight item,
per-input busy states (uploading files does not disable the URL box), and an
"n of m" batch count — but no fake percentage bar, since the wait is a Gemini
round trip, not bytes.

---

## 10. The demo seed

A reviewer arriving at an empty app gets a one-click CTA on the empty timeline
and empty graph that populates a realistic 10-document journey (2023 Python
certificate → 2026 resume and portfolio).

It inserts directly through `database.insert_document` + `embeddings.add_document`
with **no Gemini call** — categories and skills are hand-authored — so it is fast
and costs no quota. Given 20 calls/day, that is what makes it load-bearing for
any live demo.

Tuned so the graph is impressive: skills are authored so a **Python skill hub**
wires the cert → project → internship → resume chain, and every document is
written at full length so Layer-B `similar_to` edges (cosine > 0.75) actually
form — four of them, on the real embedding model.

Idempotent and non-destructive: demo documents use deterministic `demo-*` ids,
and the clear is scoped to `demo-*`, so a reviewer's own uploads survive a
re-seed.

---

## 11. Deployment traps

Both services deploy from `main` on push, described by committed config
(`vercel.json`, `render.yaml`). `vercel.json` adds no SPA rewrite on purpose: the
app is a view switch with no router.

**Two failures cost real time, and neither looked like what it was.**

1. **Both hosts suffixed the requested names.** `traceai` and `traceai-api` were
   taken, giving `trace-ai-eta.vercel.app` and `traceai-api-flmc.onrender.com`.
   `render.yaml` hardcoded the *planned* origin, so every API call was blocked.
2. **Then the fix was pasted with a trailing slash.** Browsers never send a
   trailing slash in `Origin` (it is scheme+host+port), and FastAPI compares
   exactly, so it still never matched.

Both present identically: the UI loads and does nothing ("failed to fetch"), CORS
errors only in the browser console, while `/api/health` returns `ok` and both
dashboards show green deploys. **Nothing looks broken from either end.**

The diagnostic that found it in one shot, no browser needed:

```bash
curl -si -X OPTIONS https://traceai-api-flmc.onrender.com/api/seed-demo \
  -H "Origin: https://trace-ai-eta.vercel.app" \
  -H "Access-Control-Request-Method: POST" | grep -i access-control-allow-origin
```

An allowed origin echoes back; a rejected one returns **400 with the header
absent**. Probing *variants* (with and without the slash) is what isolated it.
Preview deploys get their own `*.vercel.app` origins and are blocked the same way
until added.

Second useful trick: **`VITE_API_URL` is inlined at build time**, so you can read
the deployed bundle to see what the frontend is really calling — that is how "is
the API URL even set?" was answered without the dashboard. It also means changing
it needs a redeploy, not a restart.

**Never put anything `VITE_`-prefixed that is secret into Vercel** — Vite inlines
it into the client bundle, served to every visitor. The Gemini key belongs on
Render only (`sync: false` in the blueprint).

### Free-tier limits to know before demoing

- **No persistent disk.** `uploads/`, `data/traceai.db` and `data/chroma/` are
  wiped on every deploy and restart. Most self-heals — the vector store rebuilds
  from SQLite, and the seed re-populates with no Gemini call — but **an uploaded
  original does not come back**, so its download link 404s. That is the one place
  the preservation guarantee is bounded by the host rather than the code, and it
  is why a demo should lean on the seed profile.
- **~15-minute spin-down.** The first request after idle waits for a cold start.
  Deliberately *not* worked around with a keep-warm ping: the free allowance is
  750 instance-hours/month, which barely covers one always-on service, so pinging
  would spend the month's budget to save one wait. Hit the URL a minute before
  recording anything.
- **512 MB RAM.** The reason embeddings run on ONNX (see §6).
- **No Tesseract or Poppler**, and they cannot be installed on the native Python
  runtime, so scans always reach the Gemini Vision rung.

---

## 12. Testing

Both suites run offline by default — no network, no API quota. Backend tests use
a per-test tmp directory, so they never touch the real `uploads/`,
`data/traceai.db` or `data/chroma/`. Embeddings are stubbed with deterministic
vectors; the `model` tests opt into the real ONNX MiniLM through `embed_texts`,
the single choke point — which is how the torch→ONNX swap was checked.

`live` tests are deselected because they cost quota. They are the only tests that
catch a retired model id, a changed response shape, or an expired key — the
stubbed suite passes through all three. `network` tests are deselected too but
cost no quota; they are the only cover for real HTTP, so nothing else would catch
a changed GitHub response shape or a redirect loop that stopped following hops.

### Backend

| File | Covers |
| --- | --- |
| `test_preservation.py` | Checksums, byte-exact download, **tamper detection** |
| `test_extraction.py` | DOCX / PPTX / TXT extraction and upload error paths |
| `test_categorizer.py` | Response parsing and normalization of drifted model output |
| `test_documents_api.py` | Categorization persisted to SQLite and read back |
| `test_security.py` | Regression tests for fixed vulnerabilities |
| `test_url_guard.py` | SSRF guards — schemes, private/multicast addresses, redirect hops, size caps |
| `test_ingest_fileless.py` | URL + written-response ingestion reaching SQLite |
| `test_dates.py` | Repo creation dates, and the known-vs-assumed date flag |
| `test_github_ingest.py` | Repo enrichment, profile scraping, URL routing, the link-scheme guard |
| `test_embeddings.py` | Chunking, add/query/delete, multi-chunk dedup, **user_id isolation**, rebuild-from-SQLite |
| `test_search.py` | Query routing, `/api/search`, the category-vs-document-type mismatch, the empty-filter fallback |
| `test_relationship_engine.py` | Entity + similarity edge construction |
| `test_graph_api.py` | `/api/graph` nodes/edges, career merge, **mutation-tested user isolation** |
| `test_career_path.py` | Index mapping, clamping, never-raises, no-wipe on degrade |
| `test_degradation.py` | The reason→retryable table and exception classification |
| `test_rag.py` | Grounding, citation clamping, never-raises/degrades, `/api/answer` |
| `test_seed.py` | Endpoint, idempotency, non-destructive re-seed, the Python skill hub |
| `test_delete.py` | Deletion across SQLite, vectors, file/sidecar; **user-scoped isolation** |
| `test_category_override.py` | Taxonomy guard, relabel-only, graph re-forming on read, survival of a re-categorization, isolation |
| `test_vision.py` | All four layers — config gate, key, inline size cap, mime conversion, no-text sentinel, never-raises, key redaction, the local-first ladder, the which-rung-failed warning, a scanned upload landing searchable |
| `test_reextract.py` | Recovery of text a quota wall lost, and the four ways it must not make things worse; the structured extraction reason at the API and in the row |
| `test_identity.py` | Documents, search, graph, RAG, career paths and retry routes all scoped; cross-user read/download/delete/override each 404; the allowlist rejects path traversal; `?u=` and the header beating it. **The only file that sends two distinct ids** — every other test runs as the fallback user and would pass with isolation removed |
| `test_url_network.py` | Opt-in; real GitHub API, real redirect chain |
| `test_live_gemini.py` | Opt-in; catches a retired model id or revoked key; real career-path + RAG inference, and **real Vision OCR** |

### Frontend

Runs under jsdom with Testing Library. Nothing hits the network: `src/api/client.js`
talks to a stubbed `global.fetch`, mirroring how the backend stubs `safe_get`.

| File | Covers |
| --- | --- |
| `categories.test.js` | The palette mapping is total, unknown categories fall to neutral ink, no category resolves to the reserved career-path slate |
| `components/cardParts.test.jsx` | `formatMonth` never invents a day; `knownDate` keeps an *assumed* date out of the meta line; `formatLabel` is total |
| `api/client.test.js` | The `handle()` error contract and request shapes, incl. multipart upload |
| `components/LoadDemoButton.test.jsx` | Seed resolves before the refetch, disables in flight, a failed seed shows the error and does not refetch |
| `components/Timeline.test.jsx` | Year grouping, newest/oldest toggle, **undated-last in both directions**, present-only chips, filtering |
| `components/KnowledgeGraph.test.jsx` | `buildModel` (drops edges with a missing endpoint, dedups neighbours, bidirectional), `colorOf` / `radiusOf` / `isDashed` / `edgeId` |
| `components/AnswerCard.test.jsx` | The three honest states, and **never-fabricate** on a degraded payload |
| `components/ResultCard.test.jsx` | Retry end to end, and the file-vs-fileless download branch |
| `components/SourceRow.test.jsx` | The assumed-date rule at the row, the cited badge, the download/open branch |
| `components/Search.test.jsx` | Filter-vs-question routing, RAG grounded in returned ids, sources-only degrade, the **Ask AI** button |
| `components/Upload.test.jsx` | File / URL / text routing and **per-input independence** |
| `components/GitHubCard.test.jsx` | Repo vs profile shapes, the repo-list cap disclosure, the Upload dispatch |
| `components/TimelineEntry.test.jsx` | The two-step delete confirm, notify-parent-to-refetch, keep-and-error on failure; the category override |
| `components/QuotaNotice.test.jsx` | **Both measured limits**, the degrade-not-fail promise, the free demo seed |
| `api/userId.test.js` | Id persists, matches the backend allowlist, is replaced when corrupted, survives localStorage throwing, differs between browsers; **all 13 API calls send `X-User-Id`**; the download href carries `?u=` |

### Validate security tests by mutation

Break the guard, confirm the right test fails, restore. **Two of eight critical
assertions were hollow when first written; both looked fine in a green run.** A
green run is not evidence.

Mutation-verified so far: the doc-id guard, log redaction, the checksum
comparison, the private-address check, the multicast exclusion, the streamed size
count, the link-scheme allowlist, the reserved-path denylist, the fork exclusion,
the unknown-user fallthrough, the assumed-date flag, `embeddings.query`'s
`user_id` filter, `list_documents`' `WHERE user_id`, all ten Vision guards, both
`/reextract` rules, and each identity check.

**Findings worth keeping:**

- The **route-level SSRF test stays green if you remove *either* validation
  layer**, because `scrape_url` and `safe_get` both validate. Both are kept:
  `safe_get` covers redirect hops that `scrape_url` never sees.
- The **scheme-allowlist test was passing only because an unrelated `netloc`
  check rejected all its payloads**, so it stayed green against a
  `javascript:`-only blocklist. It now includes `ftp:` and `gopher:` cases, which
  carry a host and can therefore only be rejected by the allowlist itself.
- **A stub that *raises* "this must not be called" proves nothing here.**
  `extract_text` catches every exception from `_generate` by design, so the
  assertion would be swallowed into a degraded result and the test would pass
  whether the call happened or not. The tests use a **recorder** and assert the
  call list is empty.
- **The mutation harness patches source as bytes, and this repo mixes line
  endings** — pre-existing files are CRLF, newly added ones LF. A `\n`-written
  pattern silently fails to match a CRLF file, surfacing as "pattern not found",
  indistinguishable at a glance from a guard that moved.
- **The failed-save test asserted the old category was still *on screen*.** The
  open picker renders a chip per category, so it passed even when the badge had
  optimistically moved to a category the server rejected. It now asserts inside
  the badge's own group.
- **"A re-categorization does not revert a manual override" passed with the
  database guard removed**, because the route was *also* applying the rule to the
  value it sent. Two copies of one rule, and the test could not see which it was
  exercising. The route now reports whatever `update_categorization` stored,
  leaving one guard — which the test does redden.
- **Parametrizing a large string builds a test id from its value**, which
  overflows `PYTEST_CURRENT_TEST` on Windows (32767 chars) and errors in
  teardown. Pass `pytest.param(big, id="short-name")`.

### Two autouse stubs that will fool you

`tests/conftest.py` stubs **`categorizer.categorize()`** globally, so a test
calling the real function silently gets the stub and passes while testing
nothing. Mark such tests `@pytest.mark.nostub`. This has already produced one
false-passing security test.

It also stubs **`vision._generate`**, so no test spends real quota on an image.
It replaces only that one function, so `extract_text`'s guards still execute, and
it defaults to an **empty** transcript — failing closed, so a test needing Vision
to succeed must patch `_generate` itself and cannot pass on text a stub invented.

The same stub returns a fixed `date="2024-03"` for **every** document, so any
test about a *missing* date passes against the stub's date and never exercises
the fallback. Four tests in `test_dates.py` did exactly this before the
`no_date_found` fixture was added — copy it rather than assuming a null date.
