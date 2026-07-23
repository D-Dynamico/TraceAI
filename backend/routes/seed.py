"""Demo-profile seeding endpoint (plan.md §14).

Backs the "Load Demo Profile" button so a reviewer arriving at an empty app can
populate the timeline, graph, and search with one click. The dataset and the
insert logic live in the repo-root `seed` package (also runnable as a CLI); this
route is a thin async wrapper so the ~10 local embeds run off the event loop.

No Gemini call — the demo documents ship with hand-authored categories and
skills — so this is fast and costs no quota.
"""

from __future__ import annotations

import logging
import sys

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from config import PROJECT_ROOT

# The seed package sits at the repo root, a level above backend/; make it
# importable without depending on how uvicorn was launched.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from seed.seed_demo import load_demo  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["seed"])

DEFAULT_USER = "demo"


class SeedResponse(BaseModel):
    seeded: int
    user_id: str


@router.post("/seed-demo", response_model=SeedResponse)
async def seed_demo() -> SeedResponse:
    result = await run_in_threadpool(load_demo, DEFAULT_USER)
    logger.info("Seeded demo profile: %s documents", result["seeded"])
    return SeedResponse(**result)
