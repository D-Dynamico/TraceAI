"""TraceAI FastAPI application entry point.

Run (from the backend/ directory):
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from db import database
from routes import documents, upload

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

settings.ensure_dirs()
database.init_db()

app = FastAPI(title=settings.app_name, version="0.1.0")

# Vite dev server runs on 5173 by default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(upload.router)
app.include_router(documents.router)


@app.get("/api/health")
def health() -> dict[str, object]:
    # `ai_configured` reports whether a key is present, never the key itself.
    from ai import categorizer

    return {
        "status": "ok",
        "app": settings.app_name,
        "ai_configured": categorizer.is_configured(),
        "model": settings.gemini_model,
    }
