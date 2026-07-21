"""Search endpoint (plan.md §4 Module 5, §6 View 4).

Backend for the search view. A query is routed deterministically
(`ai/query_router`): a structured filter runs against SQLite; anything else runs
semantic vector search against Chroma and hydrates the hits back from SQLite —
so every result carries the metadata a card needs and links to its original.

This is Phase 4: it returns *ranked sources*. The RAG answer card (plan.md §4
Module 5, Path 2) is Phase 7 and is not synthesized here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ai import embeddings, query_router
from db import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])

DEFAULT_USER = "demo"
# Semantic search returns a focused top-k (plan.md §4 Module 5 uses k=5). A
# filter ("show all my certificates") wants everything, capped for safety.
DEFAULT_K = 5
MAX_K = 20
FILTER_LIMIT = 100
MAX_QUERY_CHARS = 500


class SearchRequest(BaseModel):
    query: str
    k: int = DEFAULT_K


class SearchResult(BaseModel):
    id: str
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    document_type: str | None = None
    file_type: str | None = None
    source_url: str | None = None
    effective_date: str | None = None
    date_source: str = "assumed"
    confidence: float | None = None
    # True when there is an original file to download; False for URL / text_entry
    # documents (original_path == ""). Lets the UI serve "download original" vs
    # "open source" / "view text" without re-deriving it.
    has_original: bool = False
    # Cosine similarity for a semantic hit; None for a structured filter match,
    # which is exact rather than ranked.
    score: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode: str  # "filter" | "semantic"
    category: str | None = None
    count: int
    results: list[SearchResult] = Field(default_factory=list)


def _to_result(doc: dict[str, Any], score: float | None = None) -> SearchResult:
    return SearchResult(
        id=doc["id"],
        title=doc.get("title"),
        summary=doc.get("summary"),
        category=doc.get("category"),
        document_type=doc.get("document_type"),
        file_type=doc.get("file_type"),
        source_url=doc.get("source_url"),
        effective_date=doc.get("effective_date"),
        date_source=doc.get("date_source", "assumed"),
        confidence=doc.get("confidence"),
        has_original=bool(doc.get("original_path")),
        score=score,
    )


async def _filter_search(route: query_router.Route) -> list[SearchResult]:
    """Structured, exact search over SQLite — instant, no embeddings."""
    rows = await run_in_threadpool(
        database.list_documents,
        user_id=DEFAULT_USER,
        category=route.category,
        limit=FILTER_LIMIT,
    )
    if route.sort == "latest":
        # Sort on the resolved effective_date (never the raw column), newest
        # first, unknown dates last.
        rows.sort(key=lambda d: (d.get("effective_date") or ""), reverse=True)
    return [_to_result(row) for row in rows]


async def _semantic_search(query: str, k: int) -> list[SearchResult]:
    """Vector search over Chroma, hydrated from SQLite.

    Chroma yields (doc_id, score); the full document is fetched from SQLite, the
    source of truth. A hit whose document is gone from SQLite is dropped — the
    database, not the vector store, decides what exists.
    """
    hits = await run_in_threadpool(
        embeddings.query, query, user_id=DEFAULT_USER, k=k
    )
    results: list[SearchResult] = []
    for hit in hits:
        doc = await run_in_threadpool(database.get_document, hit["doc_id"])
        if doc is None:
            continue
        results.append(_to_result(doc, score=hit["score"]))
    return results


@router.post("/search", response_model=SearchResponse)
async def search(payload: SearchRequest) -> SearchResponse:
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Query is too long (max {MAX_QUERY_CHARS} characters).",
        )

    k = max(1, min(payload.k, MAX_K))
    decision = query_router.route(query)

    if decision.mode == "filter":
        results = await _filter_search(decision)
    else:
        results = await _semantic_search(query, k)

    return SearchResponse(
        query=query,
        mode=decision.mode,
        category=decision.category,
        count=len(results),
        results=results,
    )
