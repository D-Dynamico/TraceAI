"""TraceAI FastAPI application entry point.

Run (from the backend/ directory):
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from db import database
from routes import career, documents, graph, search, seed, upload

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings.ensure_dirs()
database.init_db()

# Bring the vector store in line with SQLite (the source of truth): a deleted or
# corrupt data/chroma is rebuilt, a partial index is filled in. Cheap because
# embeddings are local — but never let a store problem stop the app from booting.
try:
    from ai import embeddings

    embeddings.ensure_synced()
except Exception:
    logging.getLogger(__name__).exception(
        "Vector store sync failed on startup — search may be degraded."
    )

app = FastAPI(title=settings.app_name, version="0.1.0")

# The Vite dev server (5173) plus whatever CORS_ORIGINS names — the deployed
# frontend is configured in the host dashboard, never hardcoded here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(graph.router)
app.include_router(career.router)
app.include_router(seed.router)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """One envelope for anything a route did not handle.

    Two things this fixes. An unhandled exception used to reach the client as
    FastAPI's bare 500 with no body a caller could branch on, while the AI paths
    already had a vocabulary for exactly this — `ai/degradation.py`'s
    `{reason, retryable}`. And several routes echoed `str(exc)` straight back,
    which on an IOError is a filesystem path from inside the container.

    So: the detail is logged, never returned. `reason` is deliberately coarse —
    a client can tell "retry might help" from "it will not", and nothing more.
    """
    logging.getLogger(__name__).exception(
        "Unhandled error on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong on the server.",
            "reason": "internal_error",
            "retryable": True,
        },
    )


@app.get("/api/health")
def health() -> JSONResponse:
    """Liveness *and* readiness: it touches the two stores that actually break.

    It used to return `status: ok` unconditionally, which made it exactly wrong
    in the one failure this app has documented: the vector-store sync above
    swallows its exception so a store problem cannot stop the app booting — and
    the health check then reported healthy while search was degraded. Render is
    wired to this path, so "ok" has to mean something.

    A broken store is reported, not raised: 503 with the parts named, so a
    reader can tell "the database is gone" from "search is degraded". The app
    still serves what it can either way.
    """
    # `ai_configured` reports whether a key is present, never the key itself.
    from ai import categorizer

    checks: dict[str, object] = {}
    try:
        with database.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = "ok"
    except Exception as exc:
        logging.getLogger(__name__).warning("Health check: database unreachable: %s", exc)
        checks["database"] = "error"

    try:
        from ai import embeddings

        checks["vector_store"] = "ok"
        checks["indexed_documents"] = embeddings.indexed_count()
    except Exception as exc:
        logging.getLogger(__name__).warning("Health check: vector store unreachable: %s", exc)
        checks["vector_store"] = "error"

    healthy = checks["database"] == "ok" and checks["vector_store"] == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "app": settings.app_name,
            "ai_configured": categorizer.is_configured(),
            "model": settings.gemini_model,
            **checks,
        },
    )
