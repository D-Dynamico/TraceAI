# TraceAI — The Knowledge Base That Reads Your Career

> **Your certificates don't talk to each other. TraceAI makes them.**
>
> TraceAI takes everything you've ever earned — a PDF certificate, a scanned marksheet, a
> GitHub repo, a portfolio link, or an achievement that only exists in your memory —
> classifies it with Gemini, embeds it for semantic search, and links it into a knowledge
> graph tracing *certification → skill → project → internship → career path*. Your
> originals are preserved byte-for-byte and every answer links back to one. Ask it a
> question in plain English; it answers from your own documents, with citations.

<sub>Built for the Wooble "AI-Powered Digital Identity" brief. Runs end-to-end on free tiers — a free-tier LLM plus a local embedding model. Apache-2.0.</sub>

**[Live demo →](https://trace-ai-eta.vercel.app)** · API:
[traceai-api-flmc.onrender.com](https://traceai-api-flmc.onrender.com)

---

## The problem

Students accumulate proof of their own work for years, and then lose track of it.

- The certificate you need for an application is in an email from 2023, or a folder called `new folder (2)`.
- A scanned marksheet is a picture of text — unsearchable by any tool you own.
- Half your best work has no document at all: club leadership, a hackathon win, volunteer work.
- Nothing knows that your Python certificate, your ML project, and your internship are the same story.

Storage can hold all of this. It cannot **understand** it. Folders don't know that a
certificate certifies a skill that a project used that an internship paid for.

## The product

One pipeline, whatever you feed it. A typed sentence and a scanned certificate come out
the other side as the same kind of record.

```
 ingest ─▶ extract ─▶ categorize ─▶ embed ─▶ relate
                                                │
              ask ◀─ cite ◀─ retrieve ◀─────────┘
```

| Stage | What happens |
|-------|--------------|
| **Ingest** | A file, a URL, or typed text. PDF · DOCX · PPTX · TXT · images; GitHub repos and profiles, portfolios, certificate pages. |
| **Extract** | Native text layer first. Failing that, a two-rung OCR ladder: local Tesseract, then Gemini Vision — so a scan is readable even on a host with no OCR binaries. |
| **Categorize** | Gemini returns type, category, title, date, summary, skills, organizations, people and tags, with a confidence score. No manual sorting, ever. |
| **Embed** | Chunked into ~900-char overlapping windows and embedded locally with all-MiniLM-L6-v2 into ChromaDB. Free, and not rate-limited. |
| **Relate** | Shared-skill hubs and cosine-similarity edges, computed on read; Gemini infers career trajectories with match scores and skill gaps. |
| **Retrieve** | A deterministic router sends filters to SQLite (instant) and real questions to vector search. |
| **Cite** | Gemini synthesizes an answer over **exactly** the sources on screen, and the rows it cited are badged so you can check it. |

**The originals never change.** Every upload is written byte-for-byte with a SHA-256
checksum verified on write and re-verified on download; everything the AI derives lands in
a separate sidecar, SQLite, or the vector store. Every timeline entry, graph node and
search result links back to a download of the untouched file.

## The constraint that shaped everything

Gemini's free tier is **5 requests per minute and 20 per day.**

Twenty calls a day is not a footnote; it is the binding constraint on the whole design:

- A scanned upload costs **two** calls, so ~26s at the limiter's 13s spacing.
- A full live-API test run is **8 calls — 40% of a day.**
- The demo script's "upload 8-10 documents" would spend a day's quota in one take.

So the architecture assumes scarcity rather than pretending otherwise. **Every Gemini caller
degrades instead of failing** — a quota wall leaves a document stored, labelled by filename,
flagged `retryable`, and repairable later; it never loses an upload. **Query understanding
is deterministic**, so "show all my certificates" costs nothing and stays instant. **Load
Demo Profile issues no Gemini call at all**, which is what makes a live demo possible on an
exhausted key. And when a scan *did* lose its Vision call, `/reextract` re-reads the
preserved original and tries again — which only works because the original was never
touched.

## See it work

Open the [live demo](https://trace-ai-eta.vercel.app) and press **Load Demo Profile** — a
realistic ten-document journey from a 2023 Python certificate to a 2026 resume, seeded with
zero AI calls. The timeline fills, the graph wires the Python skill hub through cert →
project → internship, and search answers questions about it immediately. Then upload
something of your own.

> The backend is on a free instance that sleeps after ~15 minutes idle, so the first request
> may take a moment. Free tier also means no persistent disk: the demo seed and vector store
> rebuild themselves, but an uploaded original does not survive a restart.

Locally:

```bash
git clone https://github.com/D-Dynamico/TraceAI.git && cd TraceAI
cp .env.example .env                     # add your Gemini key (free at aistudio.google.com)
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/uvicorn main:app --port 8000      # API on :8000, OpenAPI docs at /docs
cd ../frontend && npm install && npm run dev    # app on :5173

cd backend && pytest       # 465 offline tests — no network, no API quota
```

Use `.venv/bin/` instead of `.venv/Scripts/` on macOS and Linux. The `.env` goes in the
**project root**, not `backend/`.

| Variable | Required | Notes |
|----------|:---:|-------|
| `GEMINI_API_KEY` | ✅ | From [Google AI Studio](https://aistudio.google.com/apikey) |
| `GEMINI_MODEL` | — | Defaults to `gemini-3-flash-preview` |
| `VISION_OCR_ENABLED` | — | Default `true`. Off = local OCR only, so no text at all without Tesseract |
| `CORS_ORIGINS` | deploy | Browser origins allowed to call the API; Vite's dev origins always are |
| `VITE_API_URL` | deploy | **Frontend** build-time var. Never put a secret behind `VITE_` — Vite inlines those into the browser bundle |

Local OCR needs the external **Tesseract** and **Poppler** binaries. They are genuinely
optional: without them extraction falls through to Gemini Vision, which needs nothing
installed. Having them only saves a call per scanned upload.

## What you can ask it

| Query | How it's answered |
|-------|-------------------|
| "Show all my certificates" | SQLite filter — instant, zero quota, matches category **or** document type |
| "Show my AI projects" | Category + skill filter |
| "Show my latest resume" | Structured filter, sorted on the resolved date |
| "What skills did I gain in 2024?" | Vector search + a synthesized answer |
| "How does my Python cert connect to my internship?" | Vector search + RAG, grounded in the retrieved sources |

A filter that matches nothing falls back to semantic search and *says so*, rather than
showing an empty page for documents that are right there. Found the hard way: the router
guesses "resume" → *Academics*, Gemini had filed a real résumé under *Skills*, and the
filter excluded the one document the query named.

## The API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/upload` | Multipart upload → extracted text + sha256 + categorization |
| `POST` | `/api/ingest-url` | Scrape, categorize and store a URL; GitHub repos/profiles also return `details` |
| `POST` | `/api/ingest-text` | A written response — an achievement with no document |
| `POST` | `/api/search` | Routed to a SQL filter or semantic search; ranked sources |
| `POST` | `/api/answer` | RAG answer grounded in the given documents, with citations |
| `POST` | `/api/seed-demo` | Load the ten-document demo profile; idempotent, no Gemini call |
| `GET` | `/api/graph` | `{nodes, edges}` — documents, skill hubs, career paths |
| `POST` | `/api/career-paths` | Infer career trajectories over the whole profile |
| `GET` | `/api/documents` · `/api/documents/{id}` | List and detail |
| `POST` | `/api/documents/{id}/recategorize` | Re-run categorization over the preserved text |
| `POST` | `/api/documents/{id}/reextract` | Re-run **extraction** over the preserved original, then re-classify |
| `PATCH` | `/api/documents/{id}/category` | Manually overrule the AI's category |
| `DELETE` | `/api/documents/{id}` | Delete from every store — SQLite, vectors, file + sidecar |
| `GET` | `/api/documents/{id}/download` · `/verify` | The original, integrity-verified |

Every route is scoped to the caller's `X-User-Id`, so two reviewers never see each other's
library. That is **separation, not authentication** — the id is client-generated and
spoofable; it stops a collision, not an attacker.

## Built with

- **LLM** — Gemini 3 Flash on the free tier. 1M-token context with built-in vision, so one
  API reads both text and scans; the 2.0 series was retired in June 2026.
- **Embeddings** — all-MiniLM-L6-v2 through Chroma's bundled **ONNX** export, not
  sentence-transformers. Same weights, verified identical vectors, **212 MB resident vs
  torch's 439 MB** — which is what fits the 512 MB deploy target.
- **Backend** — FastAPI. Async, typed, OpenAPI docs for free; blocking work runs in a
  threadpool so one 13s-rate-limited Gemini call can't stall a health check.
- **Stores** — SQLite as the source of truth, ChromaDB as a rebuildable cache, and the
  filesystem for originals plus `.meta.json` sidecars. The sidecar deliberately duplicates
  the checksum so an original stays verifiable even if the database is lost.
- **Frontend** — React (Vite) + Tailwind. Four views, one nav, no router. Fraunces + Inter
  Tight bundled by Vite, so the deployed app makes no third-party font request.
- **Ingestion** — PyMuPDF / python-docx / python-pptx for native text, pytesseract +
  pdf2image for the free OCR rung, requests + BeautifulSoup and the GitHub REST API called
  **directly** — PyGithub would issue its own HTTP and bypass the SSRF guard.
- **Hosting** — Vercel (frontend) + Render (API), both deploying from `main` on push via
  committed `vercel.json` / `render.yaml`.

## How the codebase is organized

```
TraceAI/
├── backend/
│   ├── main.py                  # FastAPI app; CORS, startup vector-store sync
│   ├── config.py                # settings singleton, read from the root .env
│   ├── identity.py              # X-User-Id → user_id + the path-traversal allowlist
│   ├── storage.py               # originals, sidecars, checksums
│   ├── ingestion/
│   │   ├── file_parser.py       #   format detection + text extraction
│   │   ├── ocr_handler.py       #   the ladder: local Tesseract, then Gemini Vision
│   │   ├── url_guard.py         #   SSRF gate — every user-supplied fetch goes through it
│   │   ├── url_scraper.py       #   routes a URL to the right scraper
│   │   ├── github_scraper.py    #   repos + profiles, REST API called direct
│   │   ├── web_scraper.py       #   generic pages
│   │   └── text_entry.py        #   achievements with no document at all
│   ├── ai/
│   │   ├── gemini.py            #   THE shared rate limiter + key redaction
│   │   ├── categorizer.py       #   classification — must never raise
│   │   ├── vision.py            #   Vision OCR — must never raise
│   │   ├── career_path.py       #   trajectory inference (Layer C)
│   │   ├── rag.py               #   grounded answer synthesis
│   │   ├── embeddings.py        #   MiniLM via ONNX + ChromaDB
│   │   ├── precomputed.py       #   shipped vectors for the demo — skips the model entirely
│   │   ├── query_router.py      #   deterministic filter-vs-semantic routing
│   │   ├── relationship_engine.py  # entity + similarity edges (Layers A/B)
│   │   └── degradation.py       #   the reason/retryable contract every caller shares
│   ├── db/database.py           # SQLite; the single place the date fallback is applied
│   ├── graph/builder.py         # the graph, assembled on read
│   ├── routes/                  # upload · documents · search · graph · career · seed
│   └── tests/                   # 27 files, 465 offline tests
├── frontend/src/
│   ├── App.jsx                  # four views, one nav, no router
│   ├── categories.js            # the validated category palette — one source of truth
│   ├── api/                     # client.js + userId.js (per-browser identity)
│   └── components/              # Timeline · Search · Upload · KnowledgeGraph · cards
├── seed/
│   ├── seed_demo.py             # the ten-document demo profile — no Gemini call
│   └── precompute_vectors.py    # regenerates its shipped embeddings — no model call either
└── docs/                        # architecture diagrams + engineering notes
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/architecture.md](docs/architecture.md) | System, ingestion and retrieval diagrams |
| [docs/engineering-notes.md](docs/engineering-notes.md) | Every design decision and trade-off, and the traps found on the way |
| [plan.md](plan.md) | Product spec, module breakdown, phases, deliverables |
| [CLAUDE.md](CLAUDE.md) | Repo conventions and environment traps |

## Status

**Deployed and working end-to-end.** Live at
**<https://trace-ai-eta.vercel.app>**, backed by a FastAPI service on Render. Upload,
URL ingestion, written responses, OCR, categorization, semantic search, RAG answers, the
knowledge graph, career-path inference, the timeline and the demo seed are all live.

**465 backend tests offline** (plus 9 real-HTTP, 7 live-API and 3 real-embedding, deselected
by default) and
**155 frontend tests**, all green. Security- and correctness-critical assertions are
**validated by mutation** — break the guard, confirm the right test fails, restore. Two of
the first eight were hollow and looked fine in a green run; that is why the rule exists.

## License

Apache-2.0 — see [LICENSE](LICENSE).

---
