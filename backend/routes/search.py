"""Search endpoint (plan.md §4 Module 5, §6 View 4).

Backend for the search view. A query is routed deterministically
(`ai/query_router`): a structured filter runs against SQLite; anything else runs
semantic vector search against Chroma and hydrates the hits back from SQLite —
so every result carries the metadata a card needs and links to its original.

`/search` returns *ranked sources* and stays instant. The RAG answer card
(plan.md §4 Module 5, Path 2) is a separate `/answer` endpoint here: a
question-shaped query is flagged `answerable`, and the UI then fetches a
Gemini-synthesized answer over the returned sources without blocking the list.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ai import embeddings, query_router, rag
from db import database
from identity import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["search"])

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
    # True when the query is question-shaped (plan.md §6 View 4): the UI should
    # then fetch a synthesized answer from /api/answer over these results. A
    # filter query is never a question, so this is False for the grid modes.
    answerable: bool = False
    # True when a structured filter matched nothing and semantic search served
    # the query instead. The results are then *related*, not exact, and the UI
    # says so — swapping the meaning of a result set silently is worse than the
    # empty page it replaces.
    fell_back: bool = False


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


async def _filter_search(route: query_router.Route, user_id: str) -> list[SearchResult]:
    """Structured, exact search over SQLite — instant, no embeddings."""
    rows = await run_in_threadpool(
        database.list_documents,
        user_id=user_id,
        category=route.category,
        document_type=route.document_type,
        limit=FILTER_LIMIT,
    )
    if route.sort == "latest":
        # Sort on the resolved effective_date (never the raw column), newest
        # first, unknown dates last.
        rows.sort(key=lambda d: (d.get("effective_date") or ""), reverse=True)
    return [_to_result(row) for row in rows]


async def _semantic_search(query: str, k: int, user_id: str) -> list[SearchResult]:
    """Vector search over Chroma, hydrated from SQLite.

    Chroma yields (doc_id, score); the full document is fetched from SQLite, the
    source of truth. A hit whose document is gone from SQLite is dropped — the
    database, not the vector store, decides what exists.
    """
    hits = await run_in_threadpool(
        embeddings.query, query, user_id=user_id, k=k
    )
    results: list[SearchResult] = []
    for hit in hits:
        doc = await run_in_threadpool(database.get_document, hit["doc_id"])
        if doc is None:
            continue
        # The vector store is filtered by user, but hydration reads by id from
        # SQLite, which is not. Re-check here so a stale or spoofed index entry
        # cannot surface another visitor's document through the search path.
        if doc.get("user_id") != user_id:
            continue
        results.append(_to_result(doc, score=hit["score"]))
    return results


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest, user_id: str = Depends(current_user)
) -> SearchResponse:
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

    mode = decision.mode
    category = decision.category
    fell_back = False

    if decision.mode == "filter":
        results = await _filter_search(decision, user_id)
        # A filter that matched nothing falls through to semantic search rather
        # than reporting an empty library. The router's word→category guess is
        # the weakest link in the chain (it predicts what the model *should*
        # have decided), and when it guesses wrong the documents are still
        # there and still embedded — an empty page tells the user the opposite.
        # Reported honestly: the response says semantic + fell_back, because
        # these hits are related rather than exact.
        if not results:
            results = await _semantic_search(query, k, user_id)
            if results:
                mode, category, fell_back = "semantic", None, True
    else:
        results = await _semantic_search(query, k, user_id)

    return SearchResponse(
        query=query,
        mode=mode,
        category=category,
        count=len(results),
        results=results,
        answerable=query_router.is_question(query),
        fell_back=fell_back,
    )


class AnswerRequest(BaseModel):
    query: str
    # The documents the search already returned. Passing them (rather than
    # re-retrieving) guarantees the answer is grounded in exactly the sources on
    # screen and cites the visible rows, and it spends no second vector query.
    doc_ids: list[str] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    answer: str | None = None
    cited_doc_ids: list[str] = Field(default_factory=list)
    # Structured degradation (item B): null on success; a reason code + whether a
    # retry can help when synthesis degraded. The UI shows sources either way and
    # never fabricates an answer on a quota wall.
    degraded_reason: str | None = None
    retryable: bool = False


@router.post("/answer", response_model=AnswerResponse)
async def answer(
    payload: AnswerRequest, user_id: str = Depends(current_user)
) -> AnswerResponse:
    """RAG synthesis over already-retrieved sources (plan.md §4 Module 5 Path 2).

    Separate from /search so the sources render instantly while the Gemini call
    runs behind its own loading/degraded state. Hydrates the given ids from
    SQLite (the source of truth) in their given order and synthesizes; an id that
    no longer resolves is skipped.

    **The ids come from the client, so each one is re-checked against the
    caller's user.** This is the one endpoint that takes document ids as input
    rather than deriving them, which would otherwise make it the easiest way to
    read someone else's documents: post their ids and let Gemini summarize the
    contents back. Skipped silently, exactly like an id that no longer exists —
    the two are indistinguishable to a caller who should not know it is there.
    """
    query = (payload.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    if len(query) > MAX_QUERY_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Query is too long (max {MAX_QUERY_CHARS} characters).",
        )

    docs: list[dict[str, Any]] = []
    for doc_id in payload.doc_ids[:MAX_K]:
        doc = await run_in_threadpool(database.get_document, doc_id)
        if doc is not None and doc.get("user_id") == user_id:
            docs.append(doc)

    result = await run_in_threadpool(rag.synthesize, query, docs)
    return AnswerResponse(
        answer=result.answer,
        cited_doc_ids=result.cited_doc_ids,
        degraded_reason=result.degraded_reason,
        retryable=result.retryable,
    )
