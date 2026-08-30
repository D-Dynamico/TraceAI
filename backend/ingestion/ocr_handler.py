"""OCR fallback for scanned PDFs and images — a two-rung ladder.

**Rung 1, local:** pytesseract (Tesseract) with pdf2image (Poppler) to
rasterize PDF pages. Free, offline, no quota.

**Rung 2, Gemini Vision:** `ai/vision.py`, tried only when rung 1 produced
nothing. plan.md promises this fallback in §2, §4 Module 1, and § Risk
Mitigation; it did not exist until now.

Rung 2 is not a nicety. Tesseract and Poppler are **external binaries**, absent
from this dev machine and not installable on Render's free native-Python runtime
(Phase 10) — so on both, rung 1 always yields nothing. Gemini needs no binaries
and rasterizes PDF pages itself, which is why one call replaces both missing
dependencies rather than merely improving accuracy on scans.

Ordering is local-first on purpose: Tesseract costs nothing and cannot exhaust a
quota, so the rung that spends quota only runs once the free one has failed.

Nothing here raises. A failure returns empty text plus the structured reason it
is empty (`ai/degradation.py`), because the caller's job is to turn that into a
warning the user can act on — the previous version returned a bare `""`, which
left "Tesseract is missing" indistinguishable from "this page is blank".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from ai import degradation
from ai import vision
from models.document import Categorization

logger = logging.getLogger(__name__)


class OcrResult(NamedTuple):
    """Text plus how it was obtained, or why it is empty.

    `local_available` records whether Tesseract was reachable at all. It exists
    because a missing binary and a blank page produce the same empty string, and
    telling them apart is the difference between "install Tesseract" and "this
    scan is unreadable" — the diagnosis that used to be invisible.
    """

    text: str
    method: str = ""  # "ocr" (local) | "vision" | "" when nothing worked
    degraded: degradation.Degradation | None = None
    local_available: bool = False
    # The classification the Vision rung returned alongside its transcript, when
    # it managed one. None on the local rung (Tesseract classifies nothing) and
    # on a combined response whose classification half was unusable — in both
    # cases the caller classifies the text separately, as it always did.
    categorization: Categorization | None = None


def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:  # binary missing or import failure
        logger.warning("Tesseract OCR not available: %s", exc)
        return False


def _tesseract_image(path: Path) -> str:
    """Local OCR on a single image. Empty string on any failure."""
    try:
        import pytesseract
        from PIL import Image

        with Image.open(path) as img:
            return pytesseract.image_to_string(img).strip()
    except Exception as exc:
        logger.warning("Local OCR failed for image %s: %s", path, exc)
        return ""


def _tesseract_pdf(path: Path, dpi: int = 200) -> str:
    """Rasterize each PDF page (Poppler) and OCR it. Empty string on failure."""
    try:
        import pytesseract
        from pdf2image import convert_from_path

        pages = convert_from_path(str(path), dpi=dpi)
    except Exception as exc:
        logger.warning("PDF rasterization failed for %s: %s", path, exc)
        return ""

    texts: list[str] = []
    for i, page_img in enumerate(pages):
        try:
            texts.append(pytesseract.image_to_string(page_img).strip())
        except Exception as exc:
            logger.warning("OCR failed for page %d of %s: %s", i, path, exc)
    return "\n\n".join(t for t in texts if t).strip()


def _run_ladder(path: Path, local_text: str, local_available: bool) -> OcrResult:
    """Shared tail: accept local text if there is any, else try Vision.

    The Vision rung asks for the transcript *and* the classification in one
    call. That is not a shortcut around rung 1 — it runs only where rung 1
    already failed, and the ordering the mutation tests pin is unchanged. It is
    a shortcut around the *second* Gemini call the caller would otherwise make
    over the transcript, which on a Tesseract-less host was every scan's second
    13s of rate-limiter wait.
    """
    if local_text:
        return OcrResult(local_text, "ocr", None, local_available)

    result = vision.extract_and_categorize(path)
    if result.text:
        return OcrResult(
            result.text, "vision", None, local_available, result.categorization
        )

    return OcrResult("", "", result.degraded, local_available)


def ocr_image(path: Path) -> OcrResult:
    """Extract text from an image: local OCR, then Gemini Vision."""
    local_available = _tesseract_available()
    local_text = _tesseract_image(path) if local_available else ""
    return _run_ladder(path, local_text, local_available)


def ocr_pdf(path: Path, dpi: int = 200) -> OcrResult:
    """Extract text from a scanned PDF: local OCR, then Gemini Vision.

    Vision receives the PDF itself (`application/pdf`), not page images — the
    API rasterizes pages server-side, so this rung needs neither Poppler nor a
    local rasterization step.
    """
    local_available = _tesseract_available()
    local_text = _tesseract_pdf(path, dpi=dpi) if local_available else ""
    return _run_ladder(path, local_text, local_available)
