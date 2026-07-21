"""Knowledge graph endpoint (plan.md §4 Module 3, §6 View 3).

Returns the `{nodes, edges}` structure the force-directed graph renders. The
graph is built on read from SQLite + the vector store (see `graph/builder.py`);
this layer only shapes and validates the response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from graph import builder

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["graph"])

DEFAULT_USER = "demo"


class GraphNode(BaseModel):
    id: str
    type: str  # "document" | "skill" | "career_path"
    label: str
    # Document fields (absent on skill / career-path nodes).
    category: str | None = None
    document_type: str | None = None
    file_type: str | None = None
    source_url: str | None = None
    effective_date: str | None = None
    date_source: str | None = None
    has_original: bool = False
    summary: str | None = None
    # Career-path fields (Module 3 Layer C; populated in a later step).
    match_score: float | None = None
    evidence: str | None = None
    skill_gaps: str | None = None


class GraphEdge(BaseModel):
    source: str
    target: str
    relation_type: str
    weight: float = 1.0
    source_type: str | None = None
    target_type: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


@router.get("/graph", response_model=GraphResponse)
async def get_graph() -> GraphResponse:
    data = await run_in_threadpool(builder.build_graph, DEFAULT_USER)
    return GraphResponse(
        nodes=[GraphNode.model_validate(n) for n in data["nodes"]],
        edges=[GraphEdge.model_validate(e) for e in data["edges"]],
    )
