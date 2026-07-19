# TraceAI — AI-Powered Digital Identity System

Transforms fragmented academic and professional documents (certificates, resumes,
project reports, internship letters, portfolios) into a structured, searchable,
intelligent knowledge repository. See [plan.md](plan.md) for the full design.

**LLM:** Gemini 3 Flash (free tier) · **Backend:** FastAPI · **Frontend:** React + Vite + Tailwind

---

## Status

- ✅ Phase 1 — Project setup, file upload, text extraction
- ✅ **Phase 2 — Gemini categorization + SQLite storage** (current)
- ⬜ Phase 3 — URL ingestion + written-response input
- ⬜ Phase 4+ — Embeddings, relationship graph, career paths, timeline, RAG

### Phase 1 capabilities
- Upload PDF / DOCX / PPTX / TXT / images.
- Text extraction via PyMuPDF, python-docx, python-pptx, with OCR fallback
  (pytesseract) for scanned PDFs and images.
- **Original Format Preservation** — see below.
- React upload UI with drag-and-drop, per-file extraction results, warnings,
  and a download-original link.
- *Ahead of schedule:* basic URL ingestion (GitHub + generic web) already works;
  it gets split into `github_scraper` / `web_scraper` in Phase 3.

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
| POST   | `/api/ingest-url`               | `{ "url": "..." }` → scraped text                       |
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
