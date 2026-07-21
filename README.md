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
- ◑ **Phase 5 — Relationship graph + career-path inference** — backend done
  (`/api/graph`, `/api/career-paths`); the force-directed graph UI (View 3) is
  the next build
- ✅ **Phase 6 — Timeline view + search UI** (current)
- ⬜ Phase 7+ — RAG answer card, demo seed, deployment

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
- **Free-tier aware** — calls are serialized to stay within 10 RPM.

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
  windows, title prepended) and embedded with `sentence-transformers`
  (all-MiniLM-L6-v2) into ChromaDB. Embedding runs locally on CPU, so unlike
  the Gemini calls it is free and **not** rate-limited.
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
- **One shared rate limiter.** The 10 RPM free-tier budget is per-key, not
  per-module, so both Gemini callers queue through a single limiter in
  `ai/gemini.py`.
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

> **OCR (optional):** scanned-PDF / image extraction needs the external
> **Tesseract** and **Poppler** binaries on your PATH. Without them, uploads
> still succeed — OCR just returns empty text with a warning.

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

`.env` is gitignored — never commit it. For deployment (Phase 10), set these as
environment variables in the host dashboard instead of shipping the file. If a key
is ever exposed, rotate it in AI Studio rather than trying to scrub it.

Verify your config loaded (prints no secrets):

```bash
cd backend
.venv/Scripts/python.exe -c "from config import settings; print('key set:', bool(settings.gemini_api_key), '| model:', settings.gemini_model)"
```

---

## API

| Method | Endpoint                        | Description                                             |
| ------ | ------------------------------- | ------------------------------------------------------- |
| GET    | `/api/health`                   | Health check; reports whether an API key is configured   |
| POST   | `/api/upload`                   | Multipart upload → extracted text + sha256 + categorization |
| POST   | `/api/ingest-url`               | `{ "url": "..." }` → scraped text + categorization, stored; GitHub repos/profiles also return a `details` object |
| POST   | `/api/ingest-text`              | `{ "text": "..." }` → written response, categorized + stored |
| POST   | `/api/search`                   | `{ "query": "...", "k": 5 }` → routed to a SQL filter or semantic vector search; ranked source documents |
| GET    | `/api/graph`                    | `{ nodes, edges }` for the knowledge graph — documents, skill hubs, career paths, and their edges |
| POST   | `/api/career-paths`             | Infer career trajectories over the whole profile (Gemini); persists and returns them + any degradation |
| GET    | `/api/documents`                | List categorized documents; `?category=` filters        |
| GET    | `/api/documents/{id}`           | Full detail — entities, tags, extracted text            |
| POST   | `/api/documents/{id}/recategorize` | Re-run categorization over the preserved text (the retry path); updates the row in place |
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

```bash
cd backend
pytest              # 294 tests, no network, ~1 min
pytest -m network   # 9 more that make real HTTP calls (no API quota, ~7s)
pytest -m live      # 4 more that call the real Gemini API (needs a key, ~1 min)
pytest -m model     # 2 more that load the real embedding model (~80MB download first run, ~40s)
```

Tests run against a per-test tmp directory, so they never write to the real
`uploads/`, `data/traceai.db`, or `data/chroma/`. Embeddings are stubbed with
deterministic vectors by default; the `model` tests opt into the real
sentence-transformer to check its dimension and that a relevant document
actually ranks first.

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
| `test_search.py`         | Query routing (filter vs semantic) and the `/api/search` endpoint |
| `test_relationship_engine.py` | Entity + similarity edge construction (Module 3 Layers A/B) |
| `test_graph_api.py`      | `/api/graph` nodes/edges, career merge, and **mutation-tested user isolation** |
| `test_career_path.py`    | Career-path inference — index mapping, clamping, never-raises, no-wipe on degrade |
| `test_degradation.py`    | The item B contract — reason→retryable table and exception classification |
| `test_url_network.py`    | Opt-in; real GitHub API, real redirect chain               |
| `test_live_gemini.py`    | Opt-in; catches a retired model id or revoked key; real career-path inference |

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

Phase 5 added a second isolation assertion at the graph layer: breaking the
`WHERE user_id` filter in `database.list_documents` leaks a foreign document
into `GET /api/graph` and turns `test_graph_excludes_other_users_documents` red
— likewise mutation-verified.
