# Brief: rework how ingested URLs (especially GitHub repos) are presented

Planning input for TraceAI, written for an agent with no prior context on this
work. Read `plan.md` (product spec, user-owned) and `CLAUDE.md` (conventions) at
the repo root first.

**This is a presentation problem, not a "make it prettier" problem.** Two
specific complaints drive it, both from the user.

---

## 1. What exists today

One React component does all ingestion: `frontend/src/components/Upload.jsx`.
Three input controls stacked vertically, then a reverse-chronological list of
result cards:

```
┌─────────────────────────────────────────┐
│   drop zone (files)                     │
├─────────────────────────────────────────┤
│   [ URL input          ] [ Ingest ]     │
├─────────────────────────────────────────┤
│   [ textarea: written response ]        │
│                          [ Add entry ]  │
└─────────────────────────────────────────┘
   Ingested (n)
   ┌───────────────────────────────────┐
   │ card  │  card  │  card ...        │
   └───────────────────────────────────┘
```

`ResultCard` is **one generic layout used for all three input kinds**
(`kind: "file" | "url" | "text"`). Top to bottom:

| Row | Content |
|---|---|
| Heading | Gemini's `categorization.title`; falls back to filename/URL |
| Meta line | `file_type · method · document_type · date · N chars`, dot-separated |
| Right rail | "Download original" button (files only) + category badge |
| Confidence | small meter + "95% confident"; at 0.0 becomes "⚠ Unverified" |
| Summary | Gemini's 2–3 sentence summary |
| Chips | SKILLS / ORGS / PEOPLE / TAGS, muted grey pills |
| Provenance | `from <filename or URL>` (hidden for text entries) |
| Checksum | `sha256:… · original preserved` (files only) |
| Warnings | amber list, if any |
| **Preview** | `<pre>` of `text_preview`, max-height 160px, scrollable |

Cards are white on slate-100, Tailwind, light mode only (no dark mode
anywhere in the app — don't add one as a side effect).

---

## 2. The two complaints

### (a) "The description at the bottom is just scraped text"

The `<pre>` block renders `text_preview` — the first 800 chars of the raw
extraction, verbatim. For a GitHub repo that is literally:

```
Description: A simple, yet elegant, HTTP library.

Primary language: Python

Topics: client, cookies, forhumans, http, humans, python, python-requests

README:
# Requests
...
```

It reads like debug output because it is. It made sense in Phase 1 when
extraction was the only feature and you needed to see what the parser got.
Now that Gemini produces a title, summary, and entities, the raw dump is
mostly noise on the card.

**Hard constraint before redesigning this.** That same string is stored as
`documents.raw_text`, and it serves two other masters:

- it is the text sent to Gemini for categorization, and
- Phase 4 will embed it into ChromaDB for semantic search.

So **what is displayed and what is stored are separable decisions.** You may
freely change or drop the display without touching `raw_text`. Do *not*
"clean up" `raw_text` to make the card nicer — that silently degrades
categorization and future search recall. If you think `raw_text` should change,
raise it explicitly rather than folding it into a UI change.

### (b) "The GitHub part feels lacking"

Correct, and it is measurably below the project's own spec. `plan.md` §4 says
GitHub ingestion should fetch *"repo name, description, languages, topics,
README, commit history."* Implemented today: name, description, **a single**
language, topics, README. **Languages breakdown and commit history are absent.**

Worse, fields already present in the API response we *currently make* are
thrown away at zero additional cost. Measured against `github.com/psf/requests`:

| Unused field | Value | Why it matters |
|---|---|---|
| `created_at` | 2011-02-13 | **A real date.** Gemini returns `date: null` for repos, so a repo currently has no timeline position and Phase 6 would fall back to upload date — putting a 2011 project in 2026. |
| `pushed_at` | 2026-07-09 | active vs abandoned |
| `stargazers_count` | 54135 | significance signal |
| `forks_count` | 10018 | — |
| `license` | Apache-2.0 | — |
| `homepage` | readthedocs URL | often the live demo |

The `/languages` endpoint (1 extra request) returns a byte breakdown —
`Python 99.4%, Makefile 0.6%` — which is what §4 actually asks for.

**Open question worth deciding, not assuming:** a repo has fields a certificate
does not. Should `ResultCard` stay one generic layout with an optional
repo-specific section, or should GitHub get its own card component? Both are
defensible; the user has not chosen.

### (c) GitHub *profile* URLs are not handled at all

Observed in real use. The router in `backend/ingestion/url_scraper.py` matches
`/owner/repo` only (`github_scraper.REPO_PATH_RE`). A bare profile URL like
`https://github.com/D-Dynamico` does not match, so it falls through to the
generic BeautifulSoup scraper and is stored with `source_type: "web"` — no API
call, no repo list, no languages, just whatever text the HTML page rendered.

This is probably a large part of why "the GitHub part feels lacking": pasting
your own profile is the most natural thing a student does with this feature,
and it is currently the worst-handled case. A profile could plausibly yield the
repo list, pinned projects, bio, and contribution span via
`/users/{login}` and `/users/{login}/repos`.

Decide whether profile URLs are in scope. If they are, they are arguably a
*different* document than a repo — one profile could even fan out into several
project documents — which feeds directly into the card-shape question above.

### (d) Repos have no date — FIXED in commit `4d1a2c5`, kept here as background

This was open when the brief was first written and is now done; left in so the
card design knows a repo *does* have a date to display. Gemini returns
`date: null` for repos (a README does not state when the project began), so
without intervention `extracted_date` stayed NULL and `plan.md` § Risk Mitigation's
`COALESCE(extracted_date, upload_date)` fallback would stamp a repo with the day
it was ingested — `psf/requests` (created 2011-02) landing in 2026 on the very
timeline meant to show a journey.

As shipped: a repo takes its `created_at` as the date (free — already in the API
response), and any doc with no findable date is *flagged* — reads expose
`effective_date` plus `date_source` of `extracted` or `assumed`, resolved once
in `database._resolve_date`. See that commit and `test_dates.py`. For the card,
this means: show `categorization.date` (repos now have one), and when
`date_source == "assumed"` say so rather than showing a confident date.

---

## 3. Data the UI receives

All three endpoints return `categorization` with the same shape:

```jsonc
{
  "document_type": "project_report",   // certificate|resume|project_report|internship_letter|portfolio|other
  "category":      "Projects",         // Projects|Skills|Certifications|Internships|Achievements|Academics|Uncategorized
  "title":  "Requests: HTTP for Humans",
  "date":   "2024-03",                 // "YYYY" or "YYYY-MM", or null
  "summary": "...",
  "skills": [], "organizations": [], "people": [], "tags": [],
  "confidence": 0.95                   // 0.0 means "could not classify", not "0% sure"
}
```

Per-endpoint envelopes:

- `POST /api/upload` → `id, filename, stored_path, file_type, method, char_count, used_ocr, checksum, size_bytes, warnings, text_preview, categorization`
- `POST /api/ingest-url` → `id, url, title, source_type, char_count, warnings, text_preview, categorization`
  - `source_type` is `"github"` or `"web"` — **this is how the UI already knows a card is a repo.**
  - Any new repo fields (stars, languages, created_at…) must be **added to this
    response by the backend first**; they are not currently sent. Backend files:
    `backend/ingestion/github_scraper.py`, `backend/routes/upload.py`.
- `POST /api/ingest-text` → `id, filename, file_type, char_count, warnings, text_preview, categorization`

---

## 4. Constraints to respect

- **Category colors are fixed and validated.** `frontend/src/categories.js` is
  the single source of truth, shared with the future timeline and graph. The
  hex values were chosen with a palette validator, not by eye, and the file
  documents which alternatives failed. Two rules the current design satisfies
  and any redesign must keep: the category **name is always rendered as text**
  (the palette's colorblind separation is only valid with that secondary
  encoding), and the hue appears **only as a dot beside dark ink**, never as
  text color or a large fill (two hues sit below 3:1 contrast on white).
  Re-run the validator if you change a hue.
- **GitHub rate limit** is 60 requests/hour unauthenticated. Each repo costs ~3
  today (repo + README on main, then master). Adding languages + commits makes
  it 5 → about 12 repos/hour. A free personal access token raises this to 5000.
- **`PyGithub==2.5.0` is in `requirements.txt` but entirely unused** — the
  scraper uses raw `requests` REST calls. Either adopt it (plan.md specifies it)
  or drop the dependency. Note it does its own HTTP and would bypass
  `ingestion/url_guard.py`; defensible for a fixed trusted host like
  `api.github.com`, but decide deliberately.
- **All user-supplied URLs must go through `url_guard.safe_get`** (SSRF guards:
  scheme, non-public address, per-redirect-hop revalidation, 5MB body cap).
  See the Conventions section of `CLAUDE.md`.
- **No dark mode** in this app today. Don't introduce one incidentally.
- The results list is **session-only** — it shows what you ingested since page
  load. There is no "load existing documents" view yet; that arrives with the
  Phase 6 timeline. A fresh page load looks empty even with rows in the DB.

## 5. Verify in the running app, not just the build

A green `vite build` proves nothing about layout. Launch both servers and look:

```bash
cd backend && PYTHONIOENCODING=utf-8 \
  UPLOAD_DIR=<scratch>/uploads DATA_DIR=<scratch>/data DB_PATH=<scratch>/data/traceai.db \
  ./.venv/Scripts/python.exe -m uvicorn main:app --port 8000
cd frontend && npm run dev     # :5173
```

Point storage at a scratch dir as shown — the real `uploads/demo/` and
`data/traceai.db` are the user's and should stay untouched. Test URL:
`https://github.com/psf/requests`. Check 900px and ~390px widths; the current
layout has no horizontal overflow at phone width and a redesign should not
introduce one.
