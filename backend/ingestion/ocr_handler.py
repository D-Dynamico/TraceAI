"""OCR fallback for scanned PDFs and images.

Uses pytesseract (Tesseract OCR engine) with pdf2image for PDF rasterization.
Both Tesseract and Poppler are external binaries that must be installed on the
host. If they're missing we degrade gracefully: OCR returns an empty string and
logs a warning rather than crashing the upload pipeline. In Phase 2+ the Gemini
Vision API can serve as a stronger fallback when local OCR is unavailable.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:  # binary missing or import failure
        logger.warning("Tesseract OCR not available: %s", exc)
        return False


def ocr_image(path: Path) -> str:
    """Run OCR on a single image file. Returns extracted text (may be empty)."""
    if not _tesseract_available():
        return ""
    try:
        import pytesseract
        from PIL import Image

        with Image.open(path) as img:
            return pytesseract.image_to_string(img).strip()
    except Exception as exc:
        logger.warning("OCR failed for image %s: %s", path, exc)
        return ""


def ocr_pdf(path: Path, dpi: int = 200) -> str:
    """Rasterize each PDF page and OCR it. Returns concatenated page text.

    Requires Poppler (for pdf2image). Returns empty string if unavailable.
    """
    if not _tesseract_available():
        return ""
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
