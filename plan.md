# TraceAI — AI-Powered Digital Identity System

## Project Plan

---

## 1. Problem Statement

Students accumulate certificates, resumes, project reports, internship letters, portfolios, and achievements over years. This data stays scattered across folders, emails, and devices. Traditional storage cannot **understand** a person's journey.

**TraceAI** transforms fragmented academic and professional data into a structured, searchable, and intelligent knowledge repository.

### Core Principle: Original Format Preservation

The brief states this twice, so it is treated as a hard guarantee:

- Every uploaded file is stored **byte-for-byte unchanged** in `/uploads/{user_id}/`
- AI-extracted text and metadata are stored **separately** — never overwriting the original
- Every search result, timeline entry, and graph node links back to a
  **download / preview** of the original file in its native format
- A checksum (SHA-256) is stored at upload and verified on download to prove integrity

---

## 2. Tech Stack

| Layer            | Technology                                            |
| ---------------- | ----------------------------------------------------- |
| Frontend         | React (Vite) + Tailwind CSS                           |
| Backend          | Python (FastAPI)                                       |
| LLM              | Google Gemini 3 Flash — free tier (10 RPM, 1500 RPD) |
| Embeddings       | `sentence-transformers` (all-MiniLM-L6-v2) — local    |
| Vector DB        | ChromaDB                                               |
| Structured DB    | SQLite                                                 |
| OCR              | pytesseract + pdf2image (fallback for scans)           |
| Vision           | Gemini 3 Flash Vision (free) — for scanned docs/images |
| File Parsing     | PyMuPDF, python-docx, python-pptx                      |
| URL Scraping     | requests, BeautifulSoup4 (GitHub REST API, called direct) |
| Graph Viz        | react-force-graph / D3.js                              |
| Timeline         | Custom React component                                 |
| File Storage     | Local filesystem (original files preserved)            |

> **Why Gemini 3 Flash?**
> The Gemini 2.0 series was retired in June 2026. Gemini 3 Flash is Google's current
> recommended free-tier model — 10 RPM, 1,500 requests/day, 1M token context window,
> with built-in vision support. Combined with local sentence-transformers for embeddings,
> the entire AI stack runs at **zero cost**.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    FRONTEND (React)                  │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐ │
│  │  Upload   │ │ Timeline │ │ Search │ │  Graph   │ │
│  │  Module   │ │  View    │ │  Bar   │ │  View    │ │
│  └────┬─────┘ └────┬─────┘ └───┬────┘ └────┬─────┘ │
└───────┼────────────┼───────────┼────────────┼───────┘
        │            │           │            │
        ▼            ▼           ▼            ▼
┌─────────────────────────────────────────────────────┐
│                  BACKEND (FastAPI)                    │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │           API Routes                          │   │
│  │  /upload  /search  /timeline  /graph  /docs   │   │
│  └──────────────────┬───────────────────────────┘   │
│                     │                                │
│  ┌─────────────────────────────────────────────┐    │
│  │          AI Processing Pipeline              │    │
│  │                                              │    │
│  │  ┌───────────┐  ┌──────────┐  ┌──────────┐  │    │
│  │  │  Text     │  │  Gemini  │  │ Embedding│  │    │
│  │  │  Extractor│─▶│  Analyze │─▶│ Generator│  │    │
│  │  └───────────┘  └──────────┘  └──────────┘  │    │
│  └─────────────────────────────────────────────┘    │
│                     │                                │
│       ┌─────────────┼─────────────┐                  │
│       ▼             ▼             ▼                  │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐           │
│  │ SQLite  │  │ ChromaDB │  │  File    │           │
│  │ Metadata│  │ Vectors  │  │  Store   │           │
│  └─────────┘  └──────────┘  └──────────┘           │
└─────────────────────────────────────────────────────┘
```

---

## 4. Module Breakdown

### Module 1: AI Data Ingestion

**Goal:** Accept any document format and extract meaningful text.

**Supported Formats:**
- PDF (text + scanned via OCR)
- DOCX, PPTX
- Images (PNG, JPG — OCR + Gemini Vision)
- URLs (GitHub repos, portfolio sites, blogs, certificate verification links)

**Pipeline — File Uploads:**
1. User uploads file via frontend
2. Backend detects file type
3. Extract raw text using appropriate parser
4. If scanned/image → run OCR (pytesseract) or Gemini 3 Flash Vision (free)
5. Store original file in `/uploads/{user_id}/`
6. Pass extracted text to Module 2

**Pipeline — Written Response (Direct Text Entry):**
1. User types an achievement/experience directly into a text box
   - e.g. "Led the Data Science Club in 2024, organized 5 workshops"
2. No file to parse — text goes straight to Gemini categorization
3. Stored as a document with `file_type = "text_entry"`, no original file
4. **Why this matters:** Not every achievement has a certificate. Club leadership,
   hackathon wins, and volunteer work often exist only as memories.

**Pipeline — URL Inputs:**
1. User pastes a URL
2. Backend detects URL type:
   - **GitHub repo** → GitHub REST API, called directly (free, 60 req/hr unauthenticated)
     - Fetches: description, language breakdown, topics, README, stars, forks,
       license, and creation + last-push dates
     - Uses raw `requests` through `url_guard`, **not** PyGithub — PyGithub
       issues its own HTTP and would bypass the SSRF guard
   - **GitHub profile** (`github.com/<user>`) → user API + public repo list
     - One profile = one document: bio, public-repo count, top repos, languages
     - GitHub's own routes (`/pricing`, `/explore`, …) are excluded by name; an
       unknown handle falls back to the generic web scraper
   - **Portfolio / Personal Sites** → BeautifulSoup + requests
     - Extracts: text content, project titles, skills, about sections
   - **Certificate Verification (Coursera, Udemy, etc.)** → scrape verification page
     - Extracts: course name, completion date, skills, issuer
   - **Blog Posts (Medium, Dev.to, etc.)** → BeautifulSoup
     - Extracts: title, content, tags, publish date
   - **LinkedIn** → not scrapable; users upload their LinkedIn PDF export instead
3. Extracted content is fed into the same Gemini categorization pipeline as files

**Key Files:**
- `backend/ingestion/file_parser.py` — format detection + text extraction
- `backend/ingestion/ocr_handler.py` — OCR fallback logic
- `backend/ingestion/url_scraper.py` — URL type detection + routing
- `backend/ingestion/github_scraper.py` — GitHub repo metadata extraction
- `backend/ingestion/web_scraper.py` — generic webpage scraping

---

### Module 2: Intelligent Categorization

**Goal:** Auto-classify every document without manual sorting.

**Approach:** Send extracted text to Gemini API (free tier) with a structured prompt.

**Gemini Prompt Strategy:**
```
Analyze this document and return JSON:
{
  "document_type": "certificate | resume | project_report | internship_letter | portfolio | other",
  "category": "Projects | Skills | Certifications | Internships | Achievements | Academics",
  "title": "extracted or inferred title",
  "date": "YYYY-MM or YYYY if found, else null",
  "summary": "2-3 sentence summary",
  "skills": ["skill1", "skill2"],
  "organizations": ["org1", "org2"],
  "people": ["person1"],
  "tags": ["tag1", "tag2"],
  "confidence": 0.0-1.0
}
```

**Storage:** Save structured metadata in SQLite alongside file path and embedding ID.

**Key Files:**
- `backend/ai/categorizer.py` — Gemini API integration for classification
- `backend/models/document.py` — SQLite schema / ORM models
- `backend/db/database.py` — database setup and queries

---

### Module 3: Relationship Engine

**Goal:** Automatically connect related documents, skills, and experiences.

**Two-Layer Approach:**

**Layer A — Entity-Based Linking:**
- Extract entities (skills, orgs, dates) from every document
- If two documents share entities → create an edge
- Edge types: `skill_used_in`, `earned_during`, `leads_to`, `related_to`

**Layer B — Embedding Similarity:**
- Compute cosine similarity between document embeddings
- If similarity > 0.75 → create a `similar_to` edge
- Enables discovery of non-obvious connections

**Layer C — Career Path Inference:**
- Gemini analyzes the full profile (all skills + projects + internships)
- Infers likely career trajectories: `AI/ML Engineer`, `Data Analyst`, `Full-Stack Dev`
- Creates `Career Path` nodes and links internships/projects to them
- Output includes: match strength (%), supporting evidence, skill gaps
- **This completes the chain named in the brief:**
  `Certification → Skill → Project → Internship → Career Path`

**Relationship Chain (as specified in the brief):**
```
Certification ──certifies_skill──▶ Skill
Skill ──────────skill_used_in────▶ Project
Project ────────contributed_to───▶ Internship
Internship ─────leads_to─────────▶ Career Path
```

**Graph Data Structure:**
```json
{
  "nodes": [
    { "id": "doc_1", "type": "certificate", "label": "Python Cert" },
    { "id": "skill_python", "type": "skill", "label": "Python" },
    { "id": "doc_2", "type": "project", "label": "ML Pipeline" },
    { "id": "doc_3", "type": "internship", "label": "XYZ Corp Intern" },
    { "id": "career_ml", "type": "career_path", "label": "AI/ML Engineer", "match": 0.87 }
  ],
  "edges": [
    { "source": "doc_1", "target": "skill_python", "relation": "certifies_skill" },
    { "source": "skill_python", "target": "doc_2", "relation": "skill_used_in" },
    { "source": "doc_2", "target": "doc_3", "relation": "contributed_to" },
    { "source": "doc_3", "target": "career_ml", "relation": "leads_to" }
  ]
}
```

**Key Files:**
- `backend/ai/relationship_engine.py` — entity matching + similarity edges
- `backend/ai/career_path.py` — Gemini-powered career trajectory inference
- `backend/graph/builder.py` — graph construction from metadata

---

### Module 4: Digital Journey Timeline

**Goal:** Visual timeline of the user's growth.

**Data Source:** Dates extracted in Module 2, sorted chronologically.

**Frontend Component:**
- Vertical scrollable timeline
- Each entry: icon (by category) + title + date + summary
- Click to expand → shows full document + related items
- Color-coded by category:
  - 🔵 Certifications
  - 🟢 Projects
  - 🟡 Internships
  - 🟣 Achievements
  - 🔴 Academics

**API Endpoint:** `GET /api/timeline` → returns sorted list of events with metadata.

**Key Files:**
- `frontend/src/components/Timeline.jsx`
- `backend/routes/timeline.py`

---

### Module 5: Smart Retrieval System

**Goal:** Natural language search that returns relevant documents instantly.

**Hybrid Search Strategy:**

**Path 1 — Structured Query (fast, exact):**
- Parse user query → detect if it's a category filter
- "Show all certificates" → `SELECT * FROM documents WHERE category = 'Certifications'`
- "My AI projects" → `SELECT * FROM documents WHERE category = 'Projects' AND skills LIKE '%AI%'`

**Path 2 — Semantic Search (RAG):**
- Embed the user query
- Search ChromaDB for top-k similar documents (k=5)
- Pass retrieved docs + query to Gemini for a synthesized answer
- Return answer + links to source documents

**Path 3 — Hybrid (best results):**
- Use Gemini to parse query into: intent, filters, and semantic query
- Run both structured and semantic search
- Merge and rank results
- Return with original file download links

**Example Queries:**
| Query | Strategy |
|---|---|
| "Show all my certificates" | Structured filter |
| "What skills did I use in my internship?" | Semantic + RAG |
| "Show my latest resume" | Structured (sort by date) |
| "How does my Python cert relate to my projects?" | Graph traversal + RAG |

**Key Files:**
- `backend/ai/search_engine.py` — hybrid search orchestration
- `backend/ai/embeddings.py` — embedding generation + ChromaDB ops
- `backend/ai/rag.py` — RAG pipeline with Gemini

---

## 5. Database Schema (SQLite)

```sql
-- Core document metadata
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    file_type TEXT,            -- pdf, docx, image, url, text_entry
    source_url TEXT,           -- for URL-based inputs
    checksum TEXT,             -- SHA-256, proves original file integrity
    document_type TEXT,
    category TEXT,
    title TEXT,
    summary TEXT,
    extracted_date TEXT,
    upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT,
    embedding_id TEXT,
    confidence REAL,
    metadata_json TEXT
);

-- Extracted entities
CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(id),
    entity_type TEXT,  -- skill, organization, person, date
    entity_value TEXT
);

-- Relationships between documents/entities
CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    source_type TEXT,  -- document or entity
    target_id TEXT,
    target_type TEXT,
    relation_type TEXT,  -- certifies_skill, skill_used_in, similar_to, etc.
    weight REAL DEFAULT 1.0
);

-- Inferred career paths
CREATE TABLE career_paths (
    id TEXT PRIMARY KEY,
    title TEXT,                -- e.g. "AI/ML Engineer"
    match_score REAL,          -- 0.0-1.0 confidence
    evidence TEXT,             -- supporting docs/skills
    skill_gaps TEXT            -- suggested next steps
);

-- Tags for flexible categorization
CREATE TABLE tags (
    document_id TEXT REFERENCES documents(id),
    tag TEXT
);
```

---

## 6. UI Specification — What The User Actually Sees

A single web app with **one top nav bar and four views**. No sidebars, no nested menus.

```
┌──────────────────────────────────────────────────────────┐
│  ◈ TraceAI        Timeline   Graph   Search   Upload      │
├──────────────────────────────────────────────────────────┤
│                                                           │
│                    [ active view ]                        │
│                                                           │
└──────────────────────────────────────────────────────────┘
```

---

### View 1: Upload

**Purpose:** Get data in with zero friction.

**Layout:**
- Large drag-and-drop zone (accepts multi-file drop)
- Below it, two secondary inputs side by side:
  - **URL field** — "Paste a GitHub repo, portfolio, or certificate link"
  - **Text box** — "Or type an achievement that has no document"
- Processing queue below, showing live per-file status

**Processing status states (visible to user):**
```
resume.pdf        ✓ extracted → ✓ categorized → ✓ embedded → ✓ linked
ml_project.docx   ✓ extracted → ✓ categorized → ⟳ embedding...
cert_scan.jpg     ⟳ running OCR...
```

**Why this matters:** Showing the pipeline live is what makes the AI *visible*.
A silent spinner looks like file upload. A visible pipeline looks intelligent.

---

### View 2: Timeline

**Purpose:** The user's journey at a glance.

**Layout:** Vertical scroll, newest at top or oldest at top (toggle).

```
2026  ●━━  Updated Resume                    [Academics]
      │    Final year resume with 6 projects
      │
2025  ●━━  XYZ Corp Internship               [Internships]
      │    6-month data automation internship
      │
      ●━━  Hackathon Winner — TechFest        [Achievements]
      │
2024  ●━━  Machine Learning Pipeline          [Projects]
      │    End-to-end ML pipeline with pandas
      │
      ●━━  Data Science Club Lead             [Achievements]
      │
2023  ●━━  Python Programming Certificate     [Certifications]
           Coursera · verified
```

**Interaction:**
- Click any entry → expands inline to show summary, extracted skills,
  a **download original** button, and links to connected documents
- Color-coded dots by category
- Filter chips at top: `All · Certifications · Projects · Internships · Achievements · Academics`

---

### View 3: Knowledge Graph

**Purpose:** The single most visually convincing screen. This is the judge-facing view.

**Layout:** Full-width interactive force-directed graph.

**Node types (color-coded):**
| Node | Color | Shape |
|---|---|---|
| Certificate | Blue (`#2a78d6`) | Circle |
| Skill | Aqua (`#1baf7a`) | Circle (smaller) |
| Project | Green (`#008300`) | Circle |
| Internship | Yellow (`#eda100`) | Circle |
| Career Path | *needs a validated hue* | Larger circle, right side |

> Colors are the **validated category palette** in `frontend/src/categories.js` —
> the single source of truth shared with the timeline (Module 4), so a category
> is the same color everywhere. The earlier Purple / Teal / Coral here predated
> that palette and clashed with it (Projects is green, Skills is aqua), which the
> design principle "category colors are consistent everywhere" forbids. **Career
> Path** is the one node type with no category behind it, so it needs an added
> hue chosen and validated the same way (the dataviz palette validator), not
> picked by eye.

**Interaction:**
- Click a node → it and all connected nodes highlight; everything else dims to 20% opacity
- Hover → tooltip with title, date, category
- Click a document node → side panel opens with summary + download original
- Career path nodes show a match percentage badge (e.g. "AI/ML Engineer · 87%")

**The money interaction:** Click the `Python` skill node. The Coursera certificate,
the ML pipeline project, the XYZ internship, and the AI/ML Engineer career path
all light up in a connected chain. That single click tells the whole story.

---

### View 4: Search

**Purpose:** The success metric lives here.

**Layout:**
1. Search bar at top (always focused on page load)
2. **Answer card** — Gemini's synthesized response in a tinted card
3. **Sources list** — every document that informed the answer, as rows with:
   - Category icon + color
   - Title, source, date, category
   - Original format badge (`PDF` / `DOCX` / `URL` / `IMG`)
   - Download icon → serves the untouched original file
4. **Relationship path footer** — shows the chain traversed, e.g.
   `Python cert → Python skill → ML pipeline → XYZ internship → AI/ML engineer`

**Two response modes:**
- **Filter queries** ("show all my certificates") → skip the answer card,
  go straight to a result grid
- **Question queries** ("how does X connect to Y") → show answer card + sources

**Suggested queries** shown as clickable chips below an empty search bar, so
reviewers know what to try without guessing.

---

### Design Principles

| Principle | Why |
|---|---|
| Four views, one nav | A student should never feel lost. No nesting. |
| Every result downloads the original | Proves the format-preservation guarantee |
| Show the AI pipeline live during upload | Makes the intelligence visible, not hidden |
| Category colors are consistent everywhere | Timeline dot, graph node, and search icon match |
| Empty states seed the demo | "Load demo profile" button on every empty view |

---

## 7. Project Structure

```
TraceAI/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── config.py                # API keys, paths, settings
│   ├── requirements.txt
│   ├── ingestion/
│   │   ├── file_parser.py       # Multi-format text extraction
│   │   ├── ocr_handler.py       # OCR for scanned docs
│   │   ├── url_scraper.py       # URL type detection + routing
│   │   ├── github_scraper.py    # GitHub repo metadata extraction
│   │   ├── web_scraper.py       # Generic webpage scraping
│   │   └── text_entry.py        # Direct written-response handling
│   ├── ai/
│   │   ├── categorizer.py       # Gemini-powered classification
│   │   ├── embeddings.py        # Embedding generation + ChromaDB
│   │   ├── relationship_engine.py
│   │   ├── career_path.py       # Career trajectory inference
│   │   ├── search_engine.py     # Hybrid search orchestration
│   │   └── rag.py               # RAG pipeline
│   ├── db/
│   │   ├── database.py          # SQLite setup + queries
│   │   └── schema.sql
│   ├── graph/
│   │   └── builder.py           # Knowledge graph construction
│   ├── models/
│   │   └── document.py          # Pydantic models
│   └── routes/
│       ├── upload.py
│       ├── search.py
│       ├── timeline.py
│       ├── graph.py
│       └── documents.py
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── tailwind.config.js
│   ├── index.html
│   └── src/
│       ├── App.jsx
│       ├── main.jsx
│       ├── api/
│       │   └── client.js        # Axios/fetch wrapper
│       ├── components/
│       │   ├── NavBar.jsx          # Top nav, 4 views
│       │   ├── UploadZone.jsx      # Drag-drop + URL + text entry
│       │   ├── ProcessingQueue.jsx # Live pipeline status per file
│       │   ├── Timeline.jsx        # Vertical journey view
│       │   ├── TimelineEntry.jsx   # Expandable entry
│       │   ├── KnowledgeGraph.jsx  # Force-directed graph
│       │   ├── NodeDetailPanel.jsx # Side panel on node click
│       │   ├── SearchBar.jsx       # Query input + suggested chips
│       │   ├── AnswerCard.jsx      # RAG synthesized response
│       │   ├── SourceRow.jsx       # Result row + download original
│       │   ├── RelationshipPath.jsx# Chain footer
│       │   ├── CategoryChip.jsx    # Shared color-coded category tag
│       │   └── EmptyState.jsx      # "Load demo profile" CTA
│       └── pages/
│           ├── UploadPage.jsx
│           ├── TimelinePage.jsx
│           ├── GraphPage.jsx
│           └── SearchPage.jsx
├── uploads/                     # Original files stored here
├── data/
│   ├── traceai.db               # SQLite database
│   └── chroma/                  # ChromaDB vector store
├── seed/
│   ├── seed_demo.py             # Loads sample student profile
│   └── sample_docs/             # 10 realistic demo documents
├── docs/
│   ├── architecture.md
│   ├── architecture_diagram.png # Deliverable #3
│   └── thought_process.md       # Deliverable #4
├── plan.md                      # This file
└── README.md                    # Deliverable #2
```

---

## 8. Build Timeline

| Phase | Tasks | Time |
|---|---|---|
| **Phase 1** | Project setup, file upload, text extraction | 3 hours |
| **Phase 2** | Gemini categorization + SQLite storage | 3 hours |
| **Phase 3** | URL ingestion (GitHub, web) + written-response input | 2 hours |
| **Phase 4** | Embeddings + ChromaDB + semantic search | 3 hours |
| **Phase 5** | Relationship engine + career path inference + graph | 3 hours |
| **Phase 6** | Timeline view + search UI | 2 hours |
| **Phase 7** | RAG pipeline + smart retrieval polish | 2 hours |
| **Phase 8** | Sample demo dataset + seed script | 1 hour |
| **Phase 9** | UI polish, testing with real docs, edge cases | 2 hours |
| **Phase 10** | Deployment (Vercel + Render) | 2 hours |
| **Phase 11** | Demo video, README, architecture diagram, thought process | 2 hours |
| **Total** | | **~25 hours** |

---

## 9. AI/ML Techniques Used

| Technique | Where Used |
|---|---|
| **NLP** | Document text extraction, entity recognition via Gemini |
| **Embeddings** | sentence-transformers (local, free) for document vectorization |
| **Vector Database** | ChromaDB for similarity search |
| **Semantic Search** | Cosine similarity on embeddings for retrieval |
| **RAG** | Gemini + retrieved docs for intelligent Q&A |
| **Knowledge Mapping** | Entity-based graph connecting skills, projects, certs |
| **LLM Classification** | Gemini API (free tier) for auto-categorization |
| **Web Scraping** | BeautifulSoup + GitHub REST API for URL-based ingestion |

---

## 10. Demo Script

1. **Open with the pain point** — show a messy folder of 20 unsorted files (5 sec)
2. **Upload** 8-10 diverse documents at once (certs, resume, reports, internship letter)
3. **Paste a GitHub URL** — show it auto-extracts languages, README, project details
4. **Type a written response** — "Led Data Science Club in 2024" → auto-categorized
5. **Show** categorization results — no manual sorting, confidence scores visible
6. **Open** the knowledge graph — click the Python node, watch connected
   certs/projects/internships light up
7. **Show career path inference** — "AI/ML Engineer, 87% match" with evidence
8. **Scroll** the timeline — 2023 → 2026 journey at a glance
9. **Search** with natural language (the money shot):
   - "Show all my certificates" → instant results
   - "How does my Python cert connect to my internship?" → RAG answer + sources
   - "Show my latest resume" → exact file
10. **Download** an original PDF — open it, prove format is untouched
11. **Closing line:** "I never have to search through folders again."

**Target length:** 3-4 minutes. Lead with search, not setup.

---

## 11. Risk Mitigation

| Risk | Mitigation |
|---|---|
| Gemini free tier rate limits (10 RPM, 1500/day) | Cache responses, batch processing, queue uploads |
| OCR accuracy on scans | Fallback to Gemini 3 Flash Vision (free) |
| Slow embedding generation | Pre-compute on upload, async processing |
| No date in document | Use upload date as fallback, flag for user review |
| Ambiguous categorization | Show confidence score, allow manual override |
| URL scraping blocked by site | Graceful fallback — ask user to upload content manually |
| GitHub API rate limit (60/hr) | Use free personal access token for 5000/hr |

---

## 12. Required Deliverables

All four must be submitted to the Wooble portfolio.

| # | Deliverable | Format | Status |
|---|---|---|---|
| 1 | Working prototype **or** demo video | Live URL + 3-5 min video | ☐ |
| 2 | GitHub repository with README | Public repo link | ☐ |
| 3 | AI workflow / architecture diagram | PNG or embedded in README | ☐ |
| 4 | Thought process sheet | PDF or Markdown | ☐ |

**README must include:**
- Problem statement and solution overview
- Architecture diagram (embedded)
- Tech stack with justification (why Gemini free tier, why ChromaDB)
- Setup instructions (clone → install → run, under 5 commands)
- Screenshots/GIF of each module
- AI techniques used (NLP, embeddings, vector search, RAG, knowledge graph)

**Thought process sheet must cover:**
- Why this problem matters (student pain point)
- Key design decisions and tradeoffs
- Why hybrid search over pure semantic search
- Why a knowledge graph over flat tagging
- What we would build next with more time

---

## 13. Deployment Plan

A **live link beats a video** — reviewers can test it themselves.

| Component | Host | Cost |
|---|---|---|
| Frontend (React) | Vercel or Netlify | Free |
| Backend (FastAPI) | Render or Railway | Free tier |
| Vector DB | ChromaDB (persistent disk) | Free |
| SQLite | Local file on backend host | Free |

**Fallback:** If deployment proves fragile, submit a polished demo video plus
clear local setup instructions. Do not let deployment eat build time.

---

## 14. Sample Demo Dataset

Reviewers will likely arrive at an empty app. Ship a **"Load Demo Profile"** button
that seeds a realistic student journey:

| Year | Document | Category |
|---|---|---|
| 2023 | Python Programming Certificate (Coursera) | Certifications |
| 2023 | Semester 3 Marksheet | Academics |
| 2024 | Data Science Club Lead — appointment letter | Achievements |
| 2024 | Machine Learning Project Report | Projects |
| 2024 | GitHub repo link (ML pipeline) | Projects |
| 2025 | Internship Offer Letter — XYZ Corp | Internships |
| 2025 | Internship Completion Certificate | Internships |
| 2025 | Hackathon Winner Certificate | Achievements |
| 2026 | Updated Resume (PDF) | Academics |
| 2026 | Portfolio website URL | Projects |

This dataset is designed so the **relationship graph looks impressive** —
Python cert connects to the ML project, which connects to the internship,
which connects to an inferred AI/ML Engineer career path.

---

## 15. Evaluation Criteria Mapping

| Criterion | Weight | How We Address It |
|---|---|---|
| **AI organization, categorization, retrieval** | 40% | Gemini auto-categorization with confidence scores; hybrid structured + semantic search; RAG answers with source citations; zero manual sorting required |
| **AI/ML techniques** | 25% | Embeddings (sentence-transformers), vector DB (ChromaDB), semantic search (cosine similarity), RAG pipeline, NLP entity extraction, knowledge graph mapping |
| **Innovation, usefulness, UX** | 20% | Career path inference (goes beyond the brief); interactive force-directed graph; visual timeline; written-response input for undocumented achievements |
| **Clarity of explanation** | 15% | Architecture diagram, thought process sheet, well-structured README, this plan document |

**Highest-leverage focus:** The 40% criterion is retrieval quality. The demo must
show flawless natural-language search across varied document types.

---

## 16. Success Metric Checkpoint

> *"I never have to search through folders again."*

The demo delivers this moment when a user asks a question they'd normally
need to dig through folders for — and gets the exact file instantly.

**Test queries that must work flawlessly:**
- "Show all my certificates" → structured filter
- "Show my AI projects" → category + skill filter
- "Show internship documents" → structured filter
- "Show my latest resume" → structured + date sort
- "What skills did I gain in 2024?" → semantic + RAG
- "How does my Python certification connect to my internship?" → graph traversal + RAG

---

## 17. Stretch Goals (If Time Permits)

- [ ] Multi-user support with authentication
- [ ] Export portfolio as a shareable PDF/webpage
- [ ] Skill gap analysis ("based on your profile, you might want to learn...")
- [ ] Resume auto-generator from knowledge base
- [ ] Voice-based search
- [ ] Integration with LinkedIn / GitHub API for auto-import
