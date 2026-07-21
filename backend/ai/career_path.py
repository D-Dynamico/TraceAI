"""Career-path inference — Module 3 Layer C (plan.md §4).

Gemini reads the whole profile (every document's category and skills) and infers
likely career trajectories: "AI/ML Engineer · 87%", with the documents that
support each and the skills still missing. This is the chain the brief names —
`Certification → Skill → Project → Internship → Career Path` — completed at its
far end, and the graph's most convincing moment.

Like `ai/categorizer.py`, this **never raises.** It is on the same free-tier
quota, so quota exhaustion is an expected outcome; a failure returns *no* paths
plus a structured `degraded_reason` (deferred item B via `ai/degradation.py`),
never an exception that would break the graph endpoint. It shares the one global
rate limiter (`ai/gemini.py`) with the categorizer — the 10 RPM budget is
per-key, across both callers.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from typing import Any, NamedTuple

from ai import degradation, gemini
from db import database

logger = logging.getLogger(__name__)

# Below this, there is not enough profile to infer a trajectory from — skip the
# call rather than ask the model to invent one from a single document.
MIN_DOCUMENTS = 2
# Cap how many paths we keep; more than a handful clutters the graph.
MAX_PATHS = 4
# A little latitude — inference, unlike classification, benefits from it — but
# still bounded and JSON-shaped.
_GENERATION_CONFIG = {"response_mime_type": "application/json", "temperature": 0.4}

_PROMPT_TEMPLATE = """You are a career counsellor analysing a student's portfolio.

Below are the student's documents, each numbered, with its category and the \
skills it demonstrates.

{profile}

Infer up to {max_paths} realistic career paths this student is building toward.
Return ONLY a JSON object of this exact shape:
{{
  "career_paths": [
    {{
      "title": "e.g. AI/ML Engineer",
      "match_score": 0.0,
      "evidence": [0, 2],
      "skill_gaps": ["skill still to learn", "another"]
    }}
  ]
}}

Rules:
- match_score is your confidence between 0.0 and 1.0 that the student is on this path.
- evidence is a list of the DOCUMENT NUMBERS above that support the path.
- skill_gaps are concrete skills the student has NOT yet shown but would need.
- Base everything on the documents. Do not invent documents or skills.
- Prefer 2-4 strong paths over many weak ones.
"""

_rate_limiter = gemini.rate_limiter
_model = None
_model_lock = threading.Lock()


class CareerPath(NamedTuple):
    id: str
    title: str
    match_score: float
    evidence_doc_ids: list[str]
    skill_gaps: list[str]


class CareerPathInference(NamedTuple):
    paths: list[CareerPath]
    degraded_reason: str | None
    retryable: bool


class _CareerPathError(RuntimeError):
    """A response came back but could not be turned into paths."""


def _get_model():
    """Lazily build and cache this module's Gemini client (shared key config)."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            if not gemini.is_configured():
                raise RuntimeError("GEMINI_API_KEY is not set.")
            _model = gemini.build_model(_GENERATION_CONFIG)
        return _model


def _build_profile(docs: list[dict[str, Any]]) -> str:
    lines = []
    for i, doc in enumerate(docs):
        skills = ", ".join(doc.get("skills") or []) or "(none listed)"
        title = doc.get("title") or "Untitled"
        category = doc.get("category") or "Uncategorized"
        lines.append(f"[{i}] {title} — {category} — skills: {skills}")
    return "\n".join(lines)


def _parse_response(text: str) -> dict:
    cleaned = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise _CareerPathError(f"Response was not JSON: {cleaned[:200]!r}") from exc
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as inner:
            raise _CareerPathError(f"Response was not JSON: {cleaned[:200]!r}") from inner
    if not isinstance(parsed, dict):
        raise _CareerPathError(f"Expected a JSON object, got {type(parsed).__name__}.")
    return parsed


def _clamp_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:  # models sometimes answer 87 rather than 0.87
        score = score / 100.0
    return min(max(score, 0.0), 1.0)


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return []


def _build_paths(payload: dict, docs: list[dict[str, Any]]) -> list[CareerPath]:
    raw = payload.get("career_paths")
    if not isinstance(raw, list):
        return []

    paths: list[CareerPath] = []
    for item in raw[:MAX_PATHS]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        # Map the model's document numbers back to real ids, dropping any that
        # are out of range — the model occasionally cites a document that is not
        # there, and an invalid index must not fabricate an edge.
        evidence = item.get("evidence") or []
        doc_ids = [
            docs[i]["id"]
            for i in evidence
            if isinstance(i, int) and 0 <= i < len(docs)
        ]
        paths.append(
            CareerPath(
                id=uuid.uuid4().hex,
                title=title,
                match_score=_clamp_score(item.get("match_score")),
                evidence_doc_ids=doc_ids,
                skill_gaps=_coerce_str_list(item.get("skill_gaps")),
            )
        )
    return paths


def infer(user_id: str = "demo") -> CareerPathInference:
    """Infer career paths for a user's profile. Never raises.

    Returns `(paths, degraded_reason, retryable)`. On any failure — no key, a
    quota wall, a timeout, or an unparseable response — `paths` is empty and the
    reason is a structured code, so the caller can decide whether a retry helps.
    Too small a profile is not a failure: it returns no paths and no reason.
    """
    docs = database.documents_with_skills(user_id=user_id)
    if len(docs) < MIN_DOCUMENTS:
        return CareerPathInference([], None, False)

    if not gemini.is_configured():
        deg = degradation.from_reason("no_api_key")
        return CareerPathInference([], deg.reason, deg.retryable)

    prompt = _PROMPT_TEMPLATE.format(profile=_build_profile(docs), max_paths=MAX_PATHS)

    try:
        model = _get_model()
        _rate_limiter.wait()
        response = model.generate_content(prompt)
    except Exception as exc:  # network, quota, safety block, SDK change
        reason = degradation.classify_exception(exc)
        logger.warning(
            "Career-path inference failed: %s: %s",
            type(exc).__name__, gemini.redact(exc),
        )
        deg = degradation.from_reason(reason)
        return CareerPathInference([], deg.reason, deg.retryable)

    try:
        payload = _parse_response(response.text)
    except _CareerPathError as exc:
        logger.warning("Career-path response unusable: %s", gemini.redact(exc))
        deg = degradation.from_reason("unreadable_response")
        return CareerPathInference([], deg.reason, deg.retryable)

    paths = _build_paths(payload, docs)
    logger.info("Inferred %d career path(s) for %s.", len(paths), user_id)
    return CareerPathInference(paths, None, False)
