"""Gemini Vision text extraction — the OCR fallback the plan promises.

plan.md names this three times (§2 tech stack "Vision", §4 Module 1 pipeline
step 4, § Risk Mitigation "OCR accuracy on scans → fallback to Gemini 3 Flash
Vision") and it did not exist until now; `ocr_handler`'s docstring conceded as
much ("In Phase 2+ the Gemini Vision API can serve as a stronger fallback").

**Why it matters more than "accuracy on scans" suggests.** Local OCR needs
Tesseract *and* Poppler, external binaries absent from this dev machine and not
installable on Render's free native-Python runtime (Phase 10). Without a
fallback a scanned certificate is stored with empty `raw_text`: the categorizer
degrades to a filename guess, the embedding carries no signal, and the document
is unfindable — while the upload itself reports success. A silent hole in the
one guarantee the project is judged on (plan.md §15, retrieval = 40%).

Gemini needs no local binaries and rasterizes PDF pages itself, so one call
replaces both missing dependencies.

Fourth Gemini caller, same three contracts as the other three:
  - it queues through the **one shared rate limiter** in `ai/gemini.py` (the
    free-tier budget is per-key, not per-module);
  - every logged exception passes through `redact()`, because SDK errors can
    carry `?key=<api key>`;
  - it **never raises** — any failure returns empty text plus a structured
    reason from `ai/degradation.py`, so an upload is never lost to a transient
    API problem.

**Transcription, not description.** A vision model asked about a certificate
will happily *describe* one, and a plausible description of a document that was
never read is worse than no text at all: it would be embedded, searched, and
cited as if extracted. The prompt therefore demands a verbatim transcript and
defines an explicit sentinel for "nothing legible here", so an honest empty
result is distinguishable from a chatty refusal.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import NamedTuple

from ai import degradation
from ai import gemini
from config import settings
from models.document import Categorization

logger = logging.getLogger(__name__)

# Inline bytes share one budget with the prompt: the API caps a request carrying
# inline data at 20 MB total, above which the Files API is the documented path
# (not implemented — a student's certificate is orders of magnitude smaller).
# The cap here is on the *raw* bytes and deliberately well under 20 MB: whether
# the SDK's transport sends them raw or base64-encoded (which inflates by 4/3)
# is not ours to control, so the margin covers the worse case. `max_upload_bytes`
# is 25 MB, so a file can legitimately be accepted for storage and still be too
# large to send.
MAX_INLINE_BYTES = 12 * 1024 * 1024

# Mime types the API accepts directly. Anything else in file_parser's IMAGE_EXTS
# (.bmp, .tiff) is converted to PNG before sending rather than rejected.
_NATIVE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
}

# Returned verbatim by the model when a page carries no legible text. Without a
# sentinel, "I could not find any text in this image" would be stored *as* the
# document's text — prose about a failure, indexed as content.
NO_TEXT_SENTINEL = "NO_TEXT_FOUND"

PROMPT = f"""Transcribe all text visible in this document, exactly as it appears.

Rules:
- Output ONLY the transcribed text. No commentary, no summary, no markdown \
fences, no explanation of what the document is.
- Preserve reading order. Keep line breaks between distinct lines.
- Transcribe every legible element: headings, names, dates, issuer, \
signatures, seals, reference numbers, and body text.
- Do NOT infer, complete, correct, or invent text that is not legibly present. \
An illegible word is better omitted than guessed.
- Do NOT describe images, logos, or layout. Only transcribe text.
- If the document contains no legible text at all, output exactly: \
{NO_TEXT_SENTINEL}
"""

# One call that both transcribes and classifies, for the scanned path.
#
# **Why this exists.** A scan used to cost two Gemini calls — this module, then
# `ai/categorizer.py` over the transcript — serialized 13s apart by the shared
# limiter. On a host without Tesseract (Render's free tier — see the note on
# `vision_ocr_enabled` in config.py) that is *every* scanned upload: ~20s of
# wall clock and 10% of a 20-request day for one document. The second call adds
# no information the first did not already have in front of it, so it is folded
# in here.
#
# **The transcription guarantee is what this risks.** A model asked to
# summarize will happily paraphrase the transcript it was also asked for, and a
# paraphrase stored as `raw_text` is precisely the failure this module's
# docstring exists to prevent — it would be embedded, searched, and cited as if
# read. So "text" is specified as verbatim, is restated as a rule, is told
# explicitly that the summary belongs elsewhere, and keeps the sentinel.
# Temperature stays 0.0.
#
# Returns JSON rather than the bare transcript `PROMPT` asks for, so it needs
# its own generation config and its own parse.
COMBINED_PROMPT = f"""Transcribe this document, then classify what you \
transcribed. It is a document from a student's portfolio of academic and \
professional records.

Return ONLY a JSON object with exactly these keys:
{{
  "text": "the complete verbatim transcript",
  "document_type": "certificate | resume | project_report | internship_letter | portfolio | other",
  "category": "Projects | Skills | Certifications | Internships | Achievements | Academics",
  "title": "extracted or inferred title",
  "date": "YYYY-MM or YYYY if found, else null",
  "summary": "2-3 sentence summary",
  "skills": ["skill1", "skill2"],
  "organizations": ["org1", "org2"],
  "people": ["person1"],
  "tags": ["tag1", "tag2"],
  "confidence": 0.0
}}

Rules for "text" — a transcript, not a description:
- Transcribe exactly as it appears, preserving reading order and line breaks.
- Include every legible element: headings, names, dates, issuer, signatures, \
seals, reference numbers, body text.
- Do NOT infer, complete, correct, or invent text that is not legibly present. \
An illegible word is better omitted than guessed.
- Do NOT describe images, logos, or layout, and do NOT summarize here — the \
summary belongs in "summary".
- If the document contains no legible text at all, set "text" to exactly \
{NO_TEXT_SENTINEL}.

Rules for the classification, which must describe only what "text" contains:
- Use ONLY the listed values for document_type and category.
- date must be the date the document describes (issue/completion date), NOT today.
- If no date appears in the document, use null. Do not guess.
- skills are technical or professional competencies, not job titles.
- people are named individuals, excluding the document's owner where identifiable.
- confidence is your certainty in this classification, between 0.0 and 1.0.
- Extract only what the document supports. Empty lists are correct when nothing applies.
"""


class VisionError(Exception):
    """A combined transcribe-and-classify response that was not a JSON object."""


class VisionResult(NamedTuple):
    """Extracted text, or an empty string plus the reason it is empty.

    `degraded` is None on success. It carries the reason code *and* the
    `retryable` flag, so a caller can tell a quota wall (wait and retry) from a
    missing key (never will work) without parsing prose — the same contract the
    other three Gemini callers degrade through.
    """

    text: str
    degraded: degradation.Degradation | None = None


class CombinedResult(NamedTuple):
    """A transcript plus the classification of it, from one call.

    `categorization` is None whenever the classification half could not be
    used — a response that would not parse, or one whose keys the model got
    wrong. The transcript can still be good in that case, so the caller falls
    back to a separate `categorizer.categorize()` over `text` rather than
    discarding it: worst case is the two calls this replaced, never a worse
    result than before.
    """

    text: str
    categorization: Categorization | None = None
    degraded: degradation.Degradation | None = None


# Shared across every Gemini caller — see ai/gemini.py. Bound to module-local
# names so tests can monkeypatch vision._rate_limiter / vision._redact without
# reaching into another module (the pattern ai/categorizer.py established).
_rate_limiter = gemini.rate_limiter
_redact = gemini.redact

# A transcript is not a creative task; 0.0 keeps repeated reads of one scan
# consistent, which also makes a re-upload comparable.
_GENERATION_CONFIG = {"temperature": 0.0}

# The combined call returns a JSON object, so it needs `response_mime_type` —
# the same belt the categorizer wears. Temperature stays at 0.0 rather than the
# categorizer's 0.1: this response carries a verbatim transcript, and that half
# must not drift between reads of one scan.
_COMBINED_GENERATION_CONFIG = {
    "response_mime_type": "application/json",
    "temperature": 0.0,
}

_model = None
_model_lock = threading.Lock()
_combined_model = None
_combined_model_lock = threading.Lock()


def is_available() -> bool:
    """Whether a Vision call would be attempted at all."""
    return bool(settings.vision_ocr_enabled) and gemini.is_configured()


def _get_model():
    """Lazily build and cache this module's Gemini client.

    Deferred for the same reason as the categorizer's: importing this module —
    which `ingestion` does at startup — must not require a key or the SDK.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        _model = gemini.build_model(_GENERATION_CONFIG)
        return _model


def _get_combined_model():
    """The JSON-configured client, cached separately from the transcript one.

    Two clients rather than one reconfigured per call: `generation_config` is
    fixed at construction, and both shapes are used on the same process.
    """
    global _combined_model
    if _combined_model is not None:
        return _combined_model
    with _combined_model_lock:
        if _combined_model is not None:
            return _combined_model
        _combined_model = gemini.build_model(_COMBINED_GENERATION_CONFIG)
        return _combined_model


def _to_png(path: Path) -> bytes:
    """Re-encode an image the API does not accept (.bmp, .tiff) as PNG.

    Pillow is already a dependency for local OCR. Converting beats rejecting:
    a phone photo saved as TIFF is exactly the scanned-certificate case this
    module exists for.
    """
    import io

    from PIL import Image

    with Image.open(path) as img:
        buf = io.BytesIO()
        # Drop alpha/palette modes PNG would keep but the model gains nothing
        # from; RGB is the safe common denominator.
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()


def _read_payload(path: Path) -> tuple[bytes, str]:
    """Return (bytes, mime_type) ready to send. Raises on an unreadable file."""
    mime = _NATIVE_MIME_BY_EXT.get(path.suffix.lower())
    if mime:
        return path.read_bytes(), mime
    return _to_png(path), "image/png"


def _generate(data: bytes, mime_type: str) -> str:
    """The only network touch in this module — and so the seam tests stub.

    Kept separate from `extract_text` so the guards, the size cap, and the
    degradation mapping around it are all reachable offline.
    """
    model = _get_model()
    response = gemini.generate(
        model, [PROMPT, {"mime_type": mime_type, "data": data}], limiter=_rate_limiter
    )
    return (response.text or "").strip()


def _generate_combined(data: bytes, mime_type: str) -> str:
    """The combined call's network touch — the second seam tests stub.

    Separate from `_generate` so a test can drive either shape, and so the
    conftest stub that keeps the offline suite off the wire covers both.
    """
    model = _get_combined_model()
    response = gemini.generate(
        model,
        [COMBINED_PROMPT, {"mime_type": mime_type, "data": data}],
        limiter=_rate_limiter,
    )
    return (response.text or "").strip()


def _is_no_text(text: str) -> bool:
    """Whether the model reported "nothing legible" rather than a transcript."""
    return text.strip().strip('".\'').upper() == NO_TEXT_SENTINEL


def _prepare(path: Path) -> tuple[bytes, str] | degradation.Degradation:
    """Everything both entry points must check before spending a call.

    Returns the payload to send, or the reason not to send it. Shared so the
    combined path cannot quietly skip a guard the transcript path enforces —
    the size cap especially, which is the one that would otherwise fail as an
    opaque API error instead of a `too_large` a caller can explain.
    """
    if not settings.vision_ocr_enabled:
        return degradation.from_reason("vision_disabled")

    if not gemini.is_configured():
        logger.warning("Vision OCR skipped for %s — no API key.", path.name)
        return degradation.from_reason("no_api_key")

    try:
        data, mime_type = _read_payload(path)
    except Exception as exc:
        # A corrupt or non-image file. Not the model's fault and retrying will
        # not change it, which is exactly what `no_text` means here.
        logger.warning("Vision OCR could not read %s: %s", path.name, exc)
        return degradation.from_reason("no_text")

    if len(data) > MAX_INLINE_BYTES:
        logger.warning(
            "Vision OCR skipped for %s — %d bytes exceeds the %d inline limit.",
            path.name, len(data), MAX_INLINE_BYTES,
        )
        return degradation.from_reason("too_large")

    return data, mime_type


def extract_and_categorize(path: Path) -> CombinedResult:
    """Transcribe *and* classify a scan in one Gemini call. Never raises.

    The quota-cheap path for a scanned document: one call where there were two,
    which on a Tesseract-less host halves both the wall clock (~20s to ~6s) and
    the share of a 20-request day a single upload costs.

    Degrades in two directions, and they are different. A failure that produced
    no transcript returns empty text plus a reason, exactly as `extract_text`
    does — the caller cannot tell the two apart and does not need to. A response
    that transcribed but classified badly returns the text with
    `categorization=None`, which tells the caller to classify it the old way;
    that costs the second call this function exists to avoid, but only on the
    responses that earned it.
    """
    prepared = _prepare(path)
    if isinstance(prepared, degradation.Degradation):
        return CombinedResult("", None, prepared)
    data, mime_type = prepared

    try:
        raw = _generate_combined(data, mime_type)
    except Exception as exc:
        # Quota, timeout, network, a safety block, an SDK change. Redacted
        # because the message can carry the key on the REST transport.
        logger.warning(
            "Combined Vision call failed for %s: %s: %s",
            path.name, type(exc).__name__, _redact(exc),
        )
        return CombinedResult("", None, degradation.from_reason(degradation.classify_exception(exc)))

    try:
        payload = gemini.parse_json_object(raw, VisionError)
    except VisionError as exc:
        # No transcript survives an unparseable response — the text was inside
        # the JSON. Retryable, so the caller can degrade and offer a retry.
        logger.warning("Combined Vision response unreadable for %s: %s", path.name, _redact(exc))
        return CombinedResult("", None, degradation.from_reason("unreadable_response"))

    text = str(payload.pop("text", "") or "").strip()
    if not text or _is_no_text(text):
        logger.info("Combined Vision found no text in %s.", path.name)
        return CombinedResult("", None, degradation.from_reason("no_text"))

    try:
        categorization = Categorization.model_validate(payload)
    except Exception as exc:
        # The transcript is still good, and it is the half that cannot be
        # recovered by a second call. Keep it; let the caller classify.
        logger.warning(
            "Combined Vision classified %s unusably (%s) — text kept, will classify separately.",
            path.name, _redact(exc),
        )
        return CombinedResult(text, None, None)

    # A classification with nothing in it is a failure that happens to parse —
    # the same bar `categorizer.categorize` holds its own responses to.
    if not categorization.title and not categorization.summary:
        logger.warning("Combined Vision returned an empty classification for %s.", path.name)
        return CombinedResult(text, None, None)

    logger.info(
        "Combined Vision extracted %d chars from %s and classified it as %s/%s.",
        len(text), path.name, categorization.category, categorization.document_type,
    )
    return CombinedResult(text, categorization, None)


def extract_text(path: Path) -> VisionResult:
    """Transcribe a document with Gemini Vision. Never raises.

    Returns empty text plus a structured reason on every failure path: disabled
    by config, no key, too large to send inline, unreadable file, a failed call,
    or a document with nothing legible on it.
    """
    prepared = _prepare(path)
    if isinstance(prepared, degradation.Degradation):
        return VisionResult("", prepared)
    data, mime_type = prepared

    try:
        text = _generate(data, mime_type)
    except Exception as exc:
        # Quota, timeout, network, a safety block, an SDK change. Redacted
        # because the message can carry the key on the REST transport.
        logger.warning(
            "Vision OCR failed for %s: %s: %s",
            path.name, type(exc).__name__, _redact(exc),
        )
        return VisionResult("", degradation.from_reason(degradation.classify_exception(exc)))

    if not text or _is_no_text(text):
        logger.info("Vision OCR found no text in %s.", path.name)
        return VisionResult("", degradation.from_reason("no_text"))

    logger.info("Vision OCR extracted %d chars from %s.", len(text), path.name)
    return VisionResult(text, None)
