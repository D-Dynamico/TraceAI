"""Multi-format text extraction.

Detects a file's type from its extension and extracts plain text using the
appropriate parser:
  - PDF   -> PyMuPDF (fitz); OCR fallback for scanned/image-only PDFs
  - DOCX  -> python-docx
  - PPTX  -> python-pptx
  - TXT/MD-> raw read
  - Images-> OCR (pytesseract)

Returns an ExtractionResult with the text and metadata about how it was
obtained, so downstream modules (and the UI) can flag low-confidence extractions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from config import settings
from ingestion import ocr_handler

logger = logging.getLogger(__name__)

# Extension -> logical file type
PDF_EXTS = {".pdf"}
DOCX_EXTS = {".docx"}
PPTX_EXTS = {".pptx"}
TEXT_EXTS = {".txt", ".md", ".markdown"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}

SUPPORTED_EXTS = PDF_EXTS | DOCX_EXTS | PPTX_EXTS | TEXT_EXTS | IMAGE_EXTS


@dataclass
class ExtractionResult:
    text: str
    file_type: str          # "pdf" | "docx" | "pptx" | "text" | "image"
    method: str             # "native" | "ocr" | "native+ocr"
    char_count: int = 0
    used_ocr: bool = False
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.char_count = len(self.text)


class UnsupportedFileError(ValueError):
    """Raised when a file extension has no registered parser."""


def is_supported(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EXTS


def detect_file_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in PPTX_EXTS:
        return "pptx"
    if ext in TEXT_EXTS:
        return "text"
    if ext in IMAGE_EXTS:
        return "image"
    raise UnsupportedFileError(f"Unsupported file extension: {ext or '(none)'}")


def _extract_pdf(path: Path) -> ExtractionResult:
    import fitz  # PyMuPDF

    warnings: list[str] = []
    native_text = ""
    try:
        with fitz.open(path) as doc:
            native_text = "\n\n".join(page.get_text().strip() for page in doc).strip()
    except Exception as exc:
        warnings.append(f"PyMuPDF failed: {exc}")
        logger.warning("PyMuPDF extraction failed for %s: %s", path, exc)

    # If the PDF has little/no extractable text, it's likely scanned -> OCR.
    if len(native_text) < settings.ocr_char_threshold:
        ocr_text = ocr_handler.ocr_pdf(path)
        if ocr_text:
            method = "native+ocr" if native_text else "ocr"
            combined = (native_text + "\n\n" + ocr_text).strip() if native_text else ocr_text
            return ExtractionResult(combined, "pdf", method, used_ocr=True, warnings=warnings)
        if not native_text:
            warnings.append("No text extracted and OCR unavailable/empty.")
        return ExtractionResult(native_text, "pdf", "native", used_ocr=False, warnings=warnings)

    return ExtractionResult(native_text, "pdf", "native", used_ocr=False, warnings=warnings)


def _extract_docx(path: Path) -> ExtractionResult:
    import docx

    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    # Include table cell text as well.
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text.strip())
    return ExtractionResult("\n".join(parts).strip(), "docx", "native")


def _extract_pptx(path: Path) -> ExtractionResult:
    from pptx import Presentation

    prs = Presentation(str(path))
    parts: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    parts.append(text)
    return ExtractionResult("\n\n".join(parts).strip(), "pptx", "native")


def _extract_text(path: Path) -> ExtractionResult:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return ExtractionResult(text, "text", "native")


def _extract_image(path: Path) -> ExtractionResult:
    warnings: list[str] = []
    text = ocr_handler.ocr_image(path)
    if not text:
        warnings.append("OCR produced no text (Tesseract unavailable or blank image).")
    return ExtractionResult(text, "image", "ocr", used_ocr=True, warnings=warnings)


_EXTRACTORS = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "pptx": _extract_pptx,
    "text": _extract_text,
    "image": _extract_image,
}


def extract_text(path: Path) -> ExtractionResult:
    """Extract text from a file, dispatching on detected type.

    Raises UnsupportedFileError for unknown extensions.
    """
    file_type = detect_file_type(path)
    extractor = _EXTRACTORS[file_type]
    result = extractor(path)
    logger.info(
        "Extracted %d chars from %s (type=%s, method=%s, ocr=%s)",
        result.char_count, path.name, result.file_type, result.method, result.used_ocr,
    )
    return result
