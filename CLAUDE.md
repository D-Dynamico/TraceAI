# CLAUDE.md

Guidance for agents working in this repo. Product scope and phases live in
`plan.md`; setup, API, and test layout live in `README.md`. This file covers
only what those two do not: conventions and environment traps.

## Responses

Be extremely concise. Sacrifice grammar for concision and clarity. Applies to
chat replies and **commit messages**; code comments are the exception and keep
their full reasoning.

**Commit messages: a subject line plus at most a short paragraph naming the why
or the non-obvious constraint.** Not three paragraphs — long bodies bury the
point rather than preserve it. Detail that genuinely needs to live somewhere
goes in a code comment, `plan.md`, or `README.md`, all of which this repo
already uses for exactly that. Add a second paragraph only for a real trap a
future reader would otherwise hit.

## Commands

```bash
# All backend commands run from backend/. Invoke the venv python directly.
cd backend
./.venv/Scripts/python.exe -m pytest -q          # 478 offline tests, ~1.5 min
./.venv/Scripts/python.exe -m pytest -m network  # 9 real-HTTP tests, ~7s
./.venv/Scripts/python.exe -m pytest -m live     # 7 live Gemini tests, ~1 min
./.venv/Scripts/python.exe -m pytest -m model    # 3 real-embedding tests, ~40s
./.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000

cd frontend && npm run dev                       # Vite on :5173
cd frontend && npm test                          # 163 vitest tests
cd frontend && npm run lint                      # eslint, must be clean
```

`.github/workflows/ci.yml` runs the offline backend suite plus the frontend
lint, tests and build on every push and PR — both hosts auto-deploy from
`main`, so a red pipeline is the only thing standing between a bad commit and
the live site. The quota-spending markers stay deselected there.

## Environment traps

These have each cost real time. Read before running anything.

- **Windows.** Both PowerShell and a Bash tool are available; each needs its own
  syntax. **The Bash tool does not support PowerShell here-strings** (`@'...'@`)
  — using one produced a commit whose subject line was a literal `@`. Use a
  heredoc (`git commit -F - <<'EOF'`) for multi-line strings in Bash.
- **`PYTHONIOENCODING=utf-8`** or console output dies on `✓`/emoji with a
  `cp1252` `UnicodeEncodeError`.
- **Running scripts from outside `backend/`** needs `PYTHONPATH=<repo>/backend`,
  or `from main import app` fails.
- **Pass an explicit `path` to Grep.** It has defaulted to a stale cwd after a
  backgrounded command changed directories, silently returning "no matches" for
  files that exist.
- **Killing a background server by process name does not work.** Use
  `Get-NetTCPConnection -LocalPort 8000 -State Listen` → `Stop-Process`, or the
  harness's own task-stop.
- **`gh` is not installed** on this machine.

## Conventions

- `config.settings` is a module-level singleton, imported everywhere as
  `from config import settings`. Tests redirect storage by monkeypatching its
  path attributes — that works precisely because every module shares the object.
- Originals are **never** modified after being written. Anything derived goes to
  the `.meta.json` sidecar or SQLite, never back into the file. See `plan.md` §1.
- The sidecar and the database intentionally duplicate checksum and extraction
  data, so an original stays verifiable if the DB is lost. Do not "deduplicate"
  this without reading the Phase 2 commit message.
- `ai/categorizer.py::categorize()` **must never raise.** Every failure path
  degrades to a filename-based guess with `confidence = 0.0`. An upload is never
  lost to a transient API problem.
- `ai/vision.py::extract_text()` **must never raise** either, for the same
  reason — extraction is upstream of everything, so a Gemini hiccup there would
  lose the upload outright rather than degrade it.
- **Local embedding is free but not fast, and the deploy target has ~1/40th the
  CPU of this machine.** `embed_texts` therefore consults `ai/precomputed.py`
  first — shipped vectors for the demo profile's texts, whose content is a module
  constant. It covers both call sites: `add_document`'s chunk windows *and* the
  whole-`raw_text` queries `graph/builder.py` makes on every graph read. Keys are
  the SHA-256 of the exact string, so a stale table degrades to slow, never to
  wrong. **Edit `seed_demo.DOCS` or the chunking constants → rerun
  `python -m seed.precompute_vectors`** (`test_precomputed.py` fails if you
  don't). Never precompute anything whose text is not a constant.
- **OCR is local-first and must stay that way.** `ocr_handler` tries Tesseract
  before Gemini Vision because local OCR is free and cannot exhaust a quota;
  the paid rung runs only when the free one produced nothing. Reordering these
  spends quota on documents that never needed it — mutation-tested.
- Anything that logs an exception from the Gemini SDK must pass it through
  `_redact()` — on the REST transport those messages can carry `?key=<api key>`.
- Gemini free tier is **5 RPM and 20 requests per DAY** — not the "10 RPM /
  1500 RPD" this repo assumed until 2026-07-25. Both numbers come from live 429
  payloads, because the docs no longer publish per-model free-tier limits:
  `GenerateRequestsPerMinutePerProjectPerModel-FreeTier` → `limit: 5`, and
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` → `limit: 20`, model
  `gemini-3-flash`. The limiter is 13s, which handles RPM. **Nothing handles the
  20/day** — spacing cannot; see plan.md §11's unbuilt cache/batch/queue.
- **20/day is the binding constraint on everything.** Budget before spending:
  a full `pytest -m live` run is **8 calls — 40% of the day**, so two runs and
  the day is gone. A scanned upload is 2 calls. The plan.md §10 demo script
  ("upload 8-10 documents") would consume a whole day's quota by itself, which
  is why **"Load Demo Profile" makes no Gemini call at all** — Phase 8's design
  is now load-bearing, not a convenience.
- Calls are serialized by a rate limiter that holds its lock across the sleep.
  Deliberate; do not parallelize.
- **A scanned upload costs two Gemini calls** (Vision OCR, then
  categorization), so ~26s at 13s spacing. Budget for it in any test or demo
  that uploads several scans.
- **Never call `requests.get()` on a user-supplied URL.** Go through
  `ingestion/url_guard.py::safe_get`, which validates the scheme, rejects hosts
  resolving to non-public addresses, re-validates every redirect hop, and caps
  the body at 5 MB. `requests`' own redirect following defeats all of this, so
  `safe_get` passes `allow_redirects=False` and follows hops itself. It returns
  a `SafeResponse`, **not** a `requests.Response` — enforcing the size cap
  consumes the stream, which leaves the real object's `.text`/`.content`
  unusable.
- Ingestion is layered on purpose: `url_scraper` routes, `github_scraper` /
  `web_scraper` fetch, `url_guard` decides what may be fetched. `ScrapeResult`
  lives in `scrape_result.py` because `url_scraper` imports the scrapers and
  they need the type — defining it in `url_scraper` is an import cycle.
- **Never read `extracted_date` directly for display or sorting.** Use
  `effective_date` + `date_source` from `database._resolve_date`, the single
  place that applies plan.md § Risk Mitigation's upload-date fallback. A NULL
  `extracted_date` means "unknown", and reading the column raw either drops the
  document or silently dates it to its upload — which is how a repo created in
  2011 lands on the timeline today. `date_source` is `extracted` or `assumed`;
  § Risk Mitigation requires the assumed case be flagged, not just filled.
- Documents with no original file (`file_type` of `url` or `text_entry`) store
  `original_path = ""`, not NULL. The column is NOT NULL and keeping it that way
  means every reader has one code path. Their `checksum` is the SHA-256 of the
  **text**, not of a file — for a URL it pins which snapshot was ingested.
  No sidecar is written for them; there is no original to verify.

## Testing

- `tests/conftest.py` has an **autouse fixture that stubs
  `categorizer.categorize()` globally.** A test calling the real function will
  silently get the stub and pass while testing nothing. Mark such tests
  `@pytest.mark.nostub`. This has already produced one false-passing security
  test.
- `conftest.py` has a **second autouse AI stub: `vision._generate`**, the Gemini
  Vision call that extraction now reaches when local OCR yields nothing. Without
  it any test uploading an image or scanned PDF spends real quota. It replaces
  only that one function, so `extract_text`'s guards still execute, and it
  defaults to an **empty** transcript — failing closed, so a test that needs
  Vision to succeed has to patch `_generate` itself and cannot pass on text a
  stub invented.
- The same stub also returns a fixed `date="2024-03"` for **every** document, so
  any test about a *missing* date passes against the stub's date and never
  exercises the fallback. Four tests in `test_dates.py` did exactly this before
  the `no_date_found` fixture there was added — copy it rather than assuming a
  null date.
- `live` tests are deselected by default. They are the only thing that catches a
  retired model id, a revoked key, or a changed response shape — run them after
  any change under `ai/`.
- `network` tests are likewise deselected but cost no quota. Every other URL
  test stubs `safe_get`, so they are the only cover for real HTTP — run them
  after any change under `ingestion/`.
- **Parametrizing a large string builds a test id from its value**, which
  overflows the `PYTEST_CURRENT_TEST` env var on Windows (32767 chars) and
  errors in teardown. Pass `pytest.param(big, id="short-name")`.
- **Validate security tests by mutation.** Break the guard, confirm the right
  test fails, restore. Two of eight critical assertions were hollow when first
  written; both looked fine in a green run.

## Secrets

`.env` is gitignored and must stay that way. Before any commit, confirm the key
value does not appear in the staged content — not just that `.env` is absent.
Never print the key; assert on `bool(settings.gemini_api_key)` instead.
