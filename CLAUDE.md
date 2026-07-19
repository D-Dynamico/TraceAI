# CLAUDE.md

Guidance for agents working in this repo. Product scope and phases live in
`plan.md`; setup, API, and test layout live in `README.md`. This file covers
only what those two do not: conventions and environment traps.

## Commands

```bash
# All backend commands run from backend/. Invoke the venv python directly.
cd backend
./.venv/Scripts/python.exe -m pytest -q          # 76 offline tests, ~10s
./.venv/Scripts/python.exe -m pytest -m live     # 3 live Gemini tests, ~45s
./.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000

cd frontend && npm run dev                       # Vite on :5173
```

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
- Anything that logs an exception from the Gemini SDK must pass it through
  `_redact()` — on the REST transport those messages can carry `?key=<api key>`.
- Gemini free tier is 10 RPM / 1500 RPD. Calls are serialized by a rate limiter
  that holds its lock across the sleep. This is deliberate; do not parallelize.

## Testing

- `tests/conftest.py` has an **autouse fixture that stubs
  `categorizer.categorize()` globally.** A test calling the real function will
  silently get the stub and pass while testing nothing. Mark such tests
  `@pytest.mark.nostub`. This has already produced one false-passing security
  test.
- `live` tests are deselected by default. They are the only thing that catches a
  retired model id, a revoked key, or a changed response shape — run them after
  any change under `ai/`.
- **Validate security tests by mutation.** Break the guard, confirm the right
  test fails, restore. Two of eight critical assertions were hollow when first
  written; both looked fine in a green run.

## Secrets

`.env` is gitignored and must stay that way. Before any commit, confirm the key
value does not appear in the staged content — not just that `.env` is absent.
Never print the key; assert on `bool(settings.gemini_api_key)` instead.
