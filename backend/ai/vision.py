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


class VisionResult(NamedTuple):
    """Extracted text, or an empty string plus the reason it is empty.

    `degraded` is None on success. It carries the reason code *and* the
    `retryable` flag, so a caller can tell a quota wall (wait and retry) from a
    missing key (never will work) without parsing prose — the same contract the
    other three Gemini callers degrade through.
    """

    text: str
    degraded: degradation.Degradation | None = None


# Shared across every Gemini caller — see ai/gemini.py. Bound to module-local
# names so tests can monkeypatch vision._rate_limiter / vision._redact without
# reaching into another module (the pattern ai/categorizer.py established).
_rate_limiter = gemini.rate_limiter
_redact = gemini.redact

# A transcript is not a creative task; 0.0 keeps repeated reads of one scan
# consistent, which also makes a re-upload comparable.
_GENERATION_CONFIG = {"temperature": 0.0}

_model = None
_model_lock = threading.Lock()


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


def _is_no_text(text: str) -> bool:
    """Whether the model reported "nothing legible" rather than a transcript."""
    return text.strip().strip('".\'').upper() == NO_TEXT_SENTINEL


def extract_text(path: Path) -> VisionResult:
    """Transcribe a document with Gemini Vision. Never raises.

    Returns empty text plus a structured reason on every failure path: disabled
    by config, no key, too large to send inline, unreadable file, a failed call,
    or a document with nothing legible on it.
    """
    if not settings.vision_ocr_enabled:
        return VisionResult("", degradation.from_reason("vision_disabled"))

    if not gemini.is_configured():
        logger.warning("Vision OCR skipped for %s — no API key.", path.name)
        return VisionResult("", degradation.from_reason("no_api_key"))

    try:
        data, mime_type = _read_payload(path)
    except Exception as exc:
        # A corrupt or non-image file. Not the model's fault and retrying will
        # not change it, which is exactly what `no_text` means here.
        logger.warning("Vision OCR could not read %s: %s", path.name, exc)
        return VisionResult("", degradation.from_reason("no_text"))

    if len(data) > MAX_INLINE_BYTES:
        logger.warning(
            "Vision OCR skipped for %s — %d bytes exceeds the %d inline limit.",
            path.name, len(data), MAX_INLINE_BYTES,
        )
        return VisionResult("", degradation.from_reason("too_large"))

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
