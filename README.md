# TraceAI — AI-Powered Digital Identity System

Transforms fragmented academic and professional documents (certificates, resumes,
project reports, internship letters, portfolios) into a structured, searchable,
intelligent knowledge repository. See [plan.md](plan.md) for the full design.

**LLM:** Gemini 3 Flash (free tier) · **Backend:** FastAPI · **Frontend:** React + Vite + Tailwind

---

## Status

- ✅ Phase 1 — Project setup, file upload, text extraction
- ✅ Phase 2 — Gemini categorization + SQLite storage
- ✅ **Phase 3 — URL ingestion + written-response input** (current)
- ⬜ Phase 4+ — Embeddings, relationship graph, career paths, timeline, RAG

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
  threw the result away. GitHub repo URLs pull description, primary language,
  topics, and README; anything else is scraped for visible text.
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

Category colors live in `frontend/src/categories.js` — one source of truth, so
the Phase 6 timeline and Phase 5 graph color a category the same way an upload
card does. The hues follow plan.md §4 Module 4; the exact steps come from a
validated categorical palette rather than taste, and the file records the
validator results and the two candidate orderings that failed.

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
| POST   | `/api/ingest-url`               | `{ "url": "..." }` → scraped text + categorization, stored |
| POST   | `/api/ingest-text`              | `{ "text": "..." }` → written response, categorized + stored |
| GET    | `/api/documents`                | List categorized documents; `?category=` filters        |
| GET    | `/api/documents/{id}`           | Full detail — entities, tags, extracted text            |
| GET    | `/api/documents/{id}/download`  | Original file, integrity-verified                       |
| GET    | `/api/documents/{id}/verify`    | Recompute checksum, report match                        |

### Data storage

| Store                        | Holds                                                   |
| ---------------------------- | ------------------------------------------------------- |
| `uploads/{user_id}/`         | Originals, byte-for-byte unchanged                      |
| `uploads/.../{f}.meta.json`  | Sidecar — on-disk source of truth for integrity         |
| `data/traceai.db`            | SQLite — queryable metadata, entities, tags             |

The sidecar and the database are written from the same upload, deliberately
duplicating checksum and extraction data: an original plus its sidecar can be
verified even if the database is lost.

---

## Tests

```bash
cd backend
pytest              # 128 tests, no network, ~11s
pytest -m network   # 5 more that make real HTTP calls (no API quota, ~2s)
pytest -m live      # 3 more that call the real Gemini API (needs a key, ~45s)
```

Tests run against a per-test tmp directory, so they never write to the real
`uploads/` or `data/traceai.db`.

| File                     | Covers                                                    |
| ------------------------ | --------------------------------------------------------- |
| `test_preservation.py`   | The section-1 guarantee — checksums, byte-exact download, **tamper detection** |
| `test_extraction.py`     | DOCX / PPTX / TXT extraction and upload error paths        |
| `test_categorizer.py`    | Response parsing and normalization of drifted model output |
| `test_documents_api.py`  | Categorization persisted to SQLite and read back           |
| `test_security.py`       | Regression tests for fixed vulnerabilities                 |
| `test_url_guard.py`      | SSRF guards — schemes, private/multicast addresses, redirect hops, size caps |
| `test_ingest_fileless.py`| URL + written-response ingestion reaching SQLite            |
| `test_url_network.py`    | Opt-in; real GitHub API, real redirect chain               |
| `test_live_gemini.py`    | Opt-in; catches a retired model id or revoked key          |

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
