"""RAG answer synthesis — Module 5 Path 2 (plan.md §4, §6 View 4).

Given a natural-language question and the documents retrieved for it, Gemini
writes a short answer grounded ONLY in those sources and cites them by number.
This is the graded 40% moment: a question returns a synthesized answer with
citations back to the originals, not just a ranked list.

The **third** Gemini caller. Like `ai/categorizer.py` and `ai/career_path.py`
it **never raises**: any failure returns no answer plus a structured
`degraded_reason` (`ai/degradation.py`), so the search view falls back to
sources-only with an honest notice rather than a fabricated answer — the whole
point of item B. It shares the one global rate limiter (`ai/gemini.py`); the
10 RPM budget is per-key across all three callers.

Grounding is the design priority, because the 40% criterion rewards *trustworthy*
retrieval, not fluent invention: the prompt forbids using anything outside the
sources, and citation numbers that fall outside the provided set are dropped —
the same defence `career_path` applies to hallucinated evidence indices.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, NamedTuple

from ai import degradation, gemini

logger = logging.getLogger(__name__)

# How many retrieved documents to ground an answer in. Matches search's top-k
# (plan.md §4 Module 5, k=5) with a little headroom; more just dilutes the prompt.
MAX_SOURCES = 6
# Per-source text budget. Enough context to answer from without blowing the
# prompt out — the summary plus a head slice of the extracted text.
_SNIPPET_CHARS = 600
# Low temperature: an answer grounded in sources wants faithfulness, not flair.
_GENERATION_CONFIG = {"response_mime_type": "application/json", "temperature": 0.2}

_PROMPT_TEMPLATE = """You are answering a student's question using ONLY their own documents below.

Question: {query}

Sources (numbered):
{sources}

Return ONLY a JSON object of this exact shape:
{{
  "answer": "a concise 2-4 sentence answer grounded in the sources",
  "citations": [0, 2]
}}

Rules:
- Use ONLY the information in the sources. Do not invent facts, documents, skills, dates, or organizations.
- If the sources do not contain enough to answer, say so plainly in "answer" and return an empty "citations" list.
- "citations" is the list of SOURCE NUMBERS you actually used.
- Keep the answer tight and specific; name the documents and skills that connect.
"""

_rate_limiter = gemini.rate_limiter
_model = None
_model_lock = threading.Lock()


class RagAnswer(NamedTuple):
    # None when there was nothing to answer from, or when synthesis degraded.
    answer: str | None
    cited_doc_ids: list[str]
    degraded_reason: str | None
    retryable: bool


class _RagError(RuntimeError):
    """A response came back but could not be turned into an answer."""


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


def _build_sources(docs: list[dict[str, Any]]) -> str:
    lines = []
    for i, doc in enumerate(docs):
        title = doc.get("title") or doc.get("filename") or "Untitled"
        category = doc.get("category") or "Uncategorized"
        summary = (doc.get("summary") or "").strip()
        snippet = (doc.get("raw_text") or "").strip()[:_SNIPPET_CHARS]
        body = summary
        if snippet and snippet not in summary:
            body = f"{summary} {snippet}".strip() if summary else snippet
        lines.append(f"[{i}] {title} ({category}): {body}")
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
            raise _RagError(f"Response was not JSON: {cleaned[:200]!r}") from exc
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as inner:
            raise _RagError(f"Response was not JSON: {cleaned[:200]!r}") from inner
    if not isinstance(parsed, dict):
        raise _RagError(f"Expected a JSON object, got {type(parsed).__name__}.")
    return parsed


def _map_citations(raw: Any, docs: list[dict[str, Any]]) -> list[str]:
    """Map the model's source numbers back to real doc ids, dropping bad indices.

    An index outside the provided set is discarded — the model occasionally cites
    a source that is not there, and a fabricated citation must not point the UI
    at a document the answer never actually used.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    ids: list[str] = []
    for i in raw:
        if isinstance(i, int) and 0 <= i < len(docs):
            doc_id = docs[i]["id"]
            if doc_id not in seen:
                seen.add(doc_id)
                ids.append(doc_id)
    return ids


def synthesize(query: str, docs: list[dict[str, Any]]) -> RagAnswer:
    """Synthesize an answer to `query` grounded in `docs`. Never raises.

    Returns `(answer, cited_doc_ids, degraded_reason, retryable)`. An empty query
    or no documents is not a failure — it returns no answer and no reason (there
    is simply nothing to synthesize). Every real failure (no key, quota, timeout,
    unreachable, unparseable) returns no answer plus a structured reason so the
    caller can offer a retry where one would help.
    """
    query = (query or "").strip()
    if not query or not docs:
        return RagAnswer(None, [], None, False)

    if not gemini.is_configured():
        deg = degradation.from_reason("no_api_key")
        return RagAnswer(None, [], deg.reason, deg.retryable)

    sources = docs[:MAX_SOURCES]
    prompt = _PROMPT_TEMPLATE.format(query=query, sources=_build_sources(sources))

    try:
        model = _get_model()
        _rate_limiter.wait()
        response = model.generate_content(prompt)
    except Exception as exc:  # network, quota, safety block, SDK change
        reason = degradation.classify_exception(exc)
        logger.warning(
            "RAG synthesis failed: %s: %s", type(exc).__name__, gemini.redact(exc)
        )
        deg = degradation.from_reason(reason)
        return RagAnswer(None, [], deg.reason, deg.retryable)

    try:
        payload = _parse_response(response.text)
    except _RagError as exc:
        logger.warning("RAG response unusable: %s", gemini.redact(exc))
        deg = degradation.from_reason("unreadable_response")
        return RagAnswer(None, [], deg.reason, deg.retryable)

    answer = str(payload.get("answer", "")).strip() or None
    cited = _map_citations(payload.get("citations"), sources)
    logger.info("Synthesized answer for query (%d citation(s)).", len(cited))
    return RagAnswer(answer, cited, None, False)
