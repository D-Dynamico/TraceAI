"""Career-path inference endpoint (plan.md §4 Module 3 Layer C, §6 View 3).

Inference is a Gemini call over the whole profile, so it is triggered explicitly
(a button on the graph view) rather than re-run on every graph read: it costs
quota and the result is stable between uploads. The paths are persisted; the
graph endpoint merges whatever is stored. The response carries the structured
degradation contract (deferred item B) so the UI can offer a retry on a quota
wall but not on a missing key.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ai import career_path
from db import database

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["career"])

DEFAULT_USER = "demo"


class CareerPathOut(BaseModel):
    id: str
    title: str
    match_score: float
    evidence_doc_ids: list[str] = Field(default_factory=list)
    skill_gaps: list[str] = Field(default_factory=list)


class CareerPathsResponse(BaseModel):
    paths: list[CareerPathOut] = Field(default_factory=list)
    # Structured degradation (item B): null on success; a reason code + whether
    # a retry can help when inference degraded.
    degraded_reason: str | None = None
    retryable: bool = False


@router.post("/career-paths", response_model=CareerPathsResponse)
async def infer_career_paths() -> CareerPathsResponse:
    result = await run_in_threadpool(career_path.infer, DEFAULT_USER)

    # Persist only on a clean inference. A degraded run returns no paths; writing
    # that would wipe a good previous set on a transient quota wall.
    if result.degraded_reason is None:
        await run_in_threadpool(
            database.replace_career_paths,
            [p._asdict() for p in result.paths],
        )

    return CareerPathsResponse(
        paths=[CareerPathOut(**p._asdict()) for p in result.paths],
        degraded_reason=result.degraded_reason,
        retryable=result.retryable,
    )
