"""Gemini Vision OCR — the fallback plan.md promises in §2, §4 Module 1, §11.

Covers four layers, because the value of this feature is the whole chain:
`ai/vision.py` (the call and its guards), `ingestion/ocr_handler.py` (the
local-then-Vision ladder), `ingestion/file_parser.py` (the warning that finally
names *which* rung failed), and `/api/upload` (a scan lands searchable).

Every test here runs offline. `conftest.stub_vision` replaces `vision._generate`
— the only network touch — so the guards, the size cap, the sentinel, and the
exception mapping all execute for real.

Tests that need the call *attempted* set a fake key explicitly rather than
relying on the developer's `.env`: with no key, `extract_text` short-circuits at
`no_api_key` and a test asserting anything past that point would pass on a
machine with a key and fail on one without.
"""

from __future__ import annotations

import pytest

from ai import categorizer, vision
from conftest import combined_vision_response, make_image, make_textless_pdf, make_thin_text_pdf, upload
from config import settings
from db import database
from ingestion import file_parser, ocr_handler
from models.document import Categorization

TRANSCRIPT = """CERTIFICATE OF COMPLETION
Awarded to Dayanand for completing Deep Learning Specialization.
Issued by Coursera, March 2024.
"""


@pytest.fixture
def with_key(monkeypatch):
    """A configured key, so the Vision path is actually entered."""
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key-for-tests")


@pytest.fixture
def calls(monkeypatch):
    """Record every `_generate` call: (mime_type, byte count).

    A recorder, not a raising stub — `extract_text` catches every exception from
    `_generate` by design, so a stub that raised "must not be called" would be
    swallowed into a degraded result and the test would pass either way.
    """
    recorded: list[tuple[str, int]] = []

    def _fake(data: bytes, mime_type: str) -> str:
        recorded.append((mime_type, len(data)))
        return TRANSCRIPT

    def _fake_combined(data: bytes, mime_type: str) -> str:
        recorded.append((mime_type, len(data)))
        return combined_vision_response(TRANSCRIPT)

    # Both seams, one list. The ladder reaches `_generate_combined` and the
    # transcript-only entry point reaches `_generate`, but either way it is one
    # Gemini call — which is the thing every count here is asserting about.
    monkeypatch.setattr(vision, "_generate", _fake)
    monkeypatch.setattr(vision, "_generate_combined", _fake_combined)
    return recorded


# --- ai/vision.py: the call and its guards ---------------------------------


def test_transcribes_an_image(tmp_path, with_key, calls):
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.text == TRANSCRIPT.strip() or result.text == TRANSCRIPT
    assert result.degraded is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "filename, fmt, expected_mime",
    [
        ("scan.png", "PNG", "image/png"),
        ("scan.jpg", "JPEG", "image/jpeg"),
        # Neither is a mime the API accepts, so both must arrive re-encoded as
        # PNG rather than rejected — a phone photo saved as TIFF is exactly the
        # scanned-certificate case this module exists for.
        ("scan.bmp", "BMP", "image/png"),
        ("scan.tiff", "TIFF", "image/png"),
    ],
)
def test_sends_a_mime_type_the_api_accepts(
    tmp_path, with_key, calls, filename, fmt, expected_mime
):
    path = tmp_path / filename
    path.write_bytes(make_image(fmt))

    vision.extract_text(path)

    assert calls[0][0] == expected_mime


def test_a_pdf_is_sent_whole_rather_than_rasterized(tmp_path, with_key, calls):
    """The API rasterizes pages itself, which is what removes the Poppler need."""
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_textless_pdf())

    vision.extract_text(path)

    assert calls[0][0] == "application/pdf"


def test_the_no_text_sentinel_never_becomes_the_document_text(
    tmp_path, with_key, monkeypatch
):
    """A model reporting "nothing legible" must not be stored *as* the text.

    Without the sentinel the model's own prose about a failure ("I could not
    find any text in this image") would be embedded and searched as content.
    """
    monkeypatch.setattr(vision, "_generate", lambda data, mime_type: vision.NO_TEXT_SENTINEL)
    path = tmp_path / "blank.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.text == ""
    assert result.degraded is not None
    assert result.degraded.reason == "no_text"


def test_config_can_turn_vision_off_without_calling_it(
    tmp_path, with_key, calls, monkeypatch
):
    monkeypatch.setattr(settings, "vision_ocr_enabled", False)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.text == ""
    assert result.degraded.reason == "vision_disabled"
    assert result.degraded.retryable is False
    assert calls == [], "the call must be skipped, not made and discarded"


def test_a_missing_key_is_reported_as_not_retryable(tmp_path, calls, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "")
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.degraded.reason == "no_api_key"
    assert result.degraded.retryable is False
    assert calls == []


def test_a_file_over_the_inline_cap_is_not_sent(tmp_path, with_key, calls, monkeypatch):
    """The 20 MB inline request ceiling is enforced before the bytes go out.

    `max_upload_bytes` is 25 MB, so a file can be legitimately accepted for
    storage and still be too large to send — the two limits are independent.
    """
    monkeypatch.setattr(vision, "MAX_INLINE_BYTES", 10)
    path = tmp_path / "huge.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.text == ""
    assert result.degraded.reason == "too_large"
    assert result.degraded.retryable is False
    assert calls == []


def test_an_unreadable_file_degrades_instead_of_raising(tmp_path, with_key, calls):
    """A .bmp that is not an image at all. Pillow raises; nothing escapes."""
    path = tmp_path / "corrupt.bmp"
    path.write_bytes(b"this is not an image")

    result = vision.extract_text(path)

    assert result.text == ""
    assert result.degraded.reason == "no_text"
    assert calls == []


@pytest.mark.parametrize(
    "exc, expected_reason, expected_retryable",
    [
        (RuntimeError("429 Resource has been exhausted (quota)"), "quota", True),
        (RuntimeError("504 Deadline Exceeded"), "timeout", True),
        (RuntimeError("503 backend unavailable"), "unreachable", True),
    ],
)
def test_never_raises_on_a_failed_call(
    tmp_path, with_key, monkeypatch, exc, expected_reason, expected_retryable
):
    """The categorizer's guarantee, extended to extraction: an upload is never
    lost to a transient API problem."""

    def _raise(data: bytes, mime_type: str) -> str:
        raise exc

    monkeypatch.setattr(vision, "_generate", _raise)
    monkeypatch.setattr(vision, "_generate_combined", _raise)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = vision.extract_text(path)

    assert result.text == ""
    assert result.degraded.reason == expected_reason
    assert result.degraded.retryable is expected_retryable


def test_an_sdk_error_carrying_the_key_is_redacted(tmp_path, with_key, monkeypatch, caplog):
    """On the REST transport an SDK message can contain `?key=<api key>`."""

    def _raise(data: bytes, mime_type: str) -> str:
        raise RuntimeError(
            "503 POST https://generativelanguage.googleapis.com/v1/models"
            "?key=fake-key-for-tests failed"
        )

    monkeypatch.setattr(vision, "_generate", _raise)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    with caplog.at_level("WARNING"):
        vision.extract_text(path)

    assert "fake-key-for-tests" not in caplog.text
    assert "REDACTED" in caplog.text


# --- ingestion/ocr_handler.py: the ladder ----------------------------------


def test_vision_runs_only_after_local_ocr_finds_nothing(
    tmp_path, with_key, calls, monkeypatch
):
    """Local OCR costs nothing and cannot exhaust a quota, so it goes first."""
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_handler, "_tesseract_image", lambda path: "local ocr text")
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = ocr_handler.ocr_image(path)

    assert result.text == "local ocr text"
    assert result.method == "ocr"
    assert calls == [], "quota must not be spent when the free rung succeeded"


def test_the_ladder_falls_back_to_vision_when_tesseract_is_missing(
    tmp_path, with_key, calls, monkeypatch
):
    """The case on this machine, and on Render's free tier: no local binaries."""
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = ocr_handler.ocr_image(path)

    assert result.text == TRANSCRIPT.strip()
    assert result.method == "vision"
    assert result.local_available is False
    assert len(calls) == 1


def test_a_scanned_pdf_reaches_vision_without_poppler(
    tmp_path, with_key, calls, monkeypatch
):
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_textless_pdf())

    result = ocr_handler.ocr_pdf(path)

    assert result.text == TRANSCRIPT.strip()
    assert result.method == "vision"
    assert calls[0][0] == "application/pdf"


# --- ingestion/file_parser.py: the diagnosis -------------------------------


def test_an_image_extraction_reports_the_rung_that_won(tmp_path, with_key, calls, monkeypatch):
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = file_parser.extract_text(path)

    assert result.text == TRANSCRIPT.strip()
    assert result.method == "vision"
    assert result.used_ocr is True
    assert result.char_count > 0
    assert result.warnings == []


def test_a_scanned_pdf_extraction_reports_vision(tmp_path, with_key, calls, monkeypatch):
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    path = tmp_path / "scan.pdf"
    path.write_bytes(make_textless_pdf())

    result = file_parser.extract_text(path)

    assert result.text == TRANSCRIPT.strip()
    assert result.method == "vision"


def test_the_failure_warning_names_both_the_missing_binary_and_the_ai_reason(
    tmp_path, monkeypatch
):
    """The old warning was one sentence for two unrelated causes.

    "OCR produced no text (Tesseract unavailable or blank image)" left the
    operator unable to tell a missing dependency from an unreadable scan — the
    ambiguity that let a silently text-less document look like a normal one.
    """
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = file_parser.extract_text(path)

    assert result.text == ""
    warning = " ".join(result.warnings)
    assert "Tesseract" in warning
    assert "not installed" in warning
    # And the second rung's own reason, distinctly.
    assert "no API key is configured" in warning


def test_the_warning_distinguishes_a_quota_wall_from_a_missing_binary(
    tmp_path, with_key, monkeypatch
):
    def _raise(data: bytes, mime_type: str) -> str:
        raise RuntimeError("429 Resource has been exhausted (quota)")

    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: True)
    monkeypatch.setattr(ocr_handler, "_tesseract_image", lambda path: "")
    monkeypatch.setattr(vision, "_generate_combined", _raise)
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = file_parser.extract_text(path)

    warning = " ".join(result.warnings)
    assert "Local OCR found no text" in warning
    assert "not installed" not in warning, "Tesseract was present here"
    assert "quota" in warning


# --- /api/upload: a scan lands searchable ----------------------------------


def test_a_scanned_upload_lands_with_text_in_the_database(
    client, tmp_path, with_key, calls, monkeypatch
):
    """The whole point. Before Vision existed this stored `raw_text = ""`, which
    left the document unfindable by search while the upload reported success."""
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)

    resp = upload(client, "certificate_scan.png", make_image(), "image/png")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "vision"
    assert body["used_ocr"] is True
    assert body["char_count"] > 0
    assert body["warnings"] == []

    row = database.get_document(body["id"])
    assert "CERTIFICATE OF COMPLETION" in row["raw_text"]


def test_an_upload_survives_a_failed_vision_call(
    client, with_key, monkeypatch
):
    """Extraction degrading must not lose the upload — the original is stored,
    the row exists, and the warning says what happened."""

    def _raise(data: bytes, mime_type: str) -> str:
        raise RuntimeError("429 Resource has been exhausted (quota)")

    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    monkeypatch.setattr(vision, "_generate_combined", _raise)

    resp = upload(client, "certificate_scan.png", make_image(), "image/png")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["char_count"] == 0
    assert any("quota" in w for w in body["warnings"])

    row = database.get_document(body["id"])
    assert row is not None
    assert client.get(f"/api/documents/{body['id']}/verify").json()["verified"] is True


# --- One call instead of two ------------------------------------------------
#
# The scanned path used to spend two Gemini requests: this module for the
# transcript, then `ai/categorizer.py` over it. On a host without Tesseract
# that is every scan — ~20s of wall clock (two 13s limiter slots) and 10% of a
# 20-request day for one upload. These pin the merge, and pin that it degrades
# to the old two-call shape rather than to a worse result.


@pytest.fixture
def categorize_calls(monkeypatch):
    """Record separate categorization calls. Each is 5% of the day's quota."""
    calls: list[str] = []

    def _fake(text: str, filename: str = ""):
        calls.append(text)
        return Categorization(
            document_type="certificate",
            category="Certifications",
            title="Filename Guess",
            date=None,
            summary="From the second call.",
            skills=[],
            organizations=[],
            people=[],
            tags=[],
            confidence=0.5,
        )

    monkeypatch.setattr(categorizer, "categorize", _fake)
    import routes.upload as upload_route

    monkeypatch.setattr(upload_route.categorizer, "categorize", _fake)
    return calls


def test_a_scanned_upload_spends_one_gemini_call_not_two(
    client, with_key, calls, categorize_calls, monkeypatch
):
    """The whole point of the merge, measured in requests rather than seconds.

    Wall clock is the symptom; the request count is the cause, and it is the one
    that binds — the free tier allows 20 a day.
    """
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)

    resp = upload(client, "certificate_scan.png", make_image(), "image/png")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1, "exactly one Vision call"
    assert categorize_calls == [], "and no separate categorization call"

    body = resp.json()
    assert body["categorization"]["title"] == "Scanned Certificate"
    assert body["categorization"]["summary"] == "A certificate read from a scan."

    row = database.get_document(body["id"])
    assert "CERTIFICATE OF COMPLETION" in row["raw_text"]
    assert row["title"] == "Scanned Certificate"


def test_an_unusable_classification_keeps_the_transcript_and_classifies_separately(
    client, with_key, categorize_calls, monkeypatch
):
    """Half a good response is still worth the call that produced it.

    The transcript is the half a second call cannot recover — it would need the
    pixels again. So a response that transcribes but classifies badly keeps the
    text and pays for the categorization the old way, which is exactly the
    behaviour that existed before the merge.
    """
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    monkeypatch.setattr(
        vision, "_generate_combined",
        # A transcript with an empty classification beside it. `Categorization`
        # is deliberately forgiving — unknown values coerce rather than raise —
        # so this, not a validation error, is what an unusable classification
        # actually looks like: it is the bar `categorizer.categorize` holds its
        # own responses to.
        lambda data, mime_type: combined_vision_response(TRANSCRIPT, title="", summary=""),
    )

    resp = upload(client, "certificate_scan.png", make_image(), "image/png")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "CERTIFICATE OF COMPLETION" in body["text_preview"]
    assert len(categorize_calls) == 1, "the classification half is retried separately"
    assert "CERTIFICATE" in categorize_calls[0], "over the recovered text"
    assert body["categorization"]["title"] == "Filename Guess"


def test_an_unparseable_combined_response_degrades_like_any_other_failure(
    tmp_path, with_key, monkeypatch
):
    """No transcript survives a response that is not JSON — it was inside it."""
    monkeypatch.setattr(
        vision, "_generate_combined", lambda data, mime_type: "I'm afraid I can't do that"
    )
    path = tmp_path / "scan.png"
    path.write_bytes(make_image())

    result = vision.extract_and_categorize(path)

    assert result.text == ""
    assert result.categorization is None
    assert result.degraded.reason == "unreadable_response"
    assert result.degraded.retryable is True


def test_the_sentinel_still_holds_on_the_combined_call(tmp_path, with_key, monkeypatch):
    """"Nothing legible" must not become the document's text here either.

    The merge gave the model a second job, and a model with a summary to write
    has a reason to describe a page it could not read. The sentinel is the guard
    against that, so it is asserted on both call shapes.
    """
    monkeypatch.setattr(
        vision, "_generate_combined",
        lambda data, mime_type: combined_vision_response(vision.NO_TEXT_SENTINEL),
    )
    path = tmp_path / "blank.png"
    path.write_bytes(make_image())

    result = vision.extract_and_categorize(path)

    assert result.text == ""
    assert result.categorization is None, (
        "a classification of a page with no text is invention, whatever it says"
    )
    assert result.degraded.reason == "no_text"


def test_the_combined_call_keeps_the_guards_the_transcript_call_has(
    tmp_path, with_key, monkeypatch
):
    """Both entry points share `_prepare`, so neither can drift past a guard."""
    sent: list[str] = []
    monkeypatch.setattr(
        vision, "_generate_combined",
        lambda data, mime_type: sent.append(mime_type) or combined_vision_response(TRANSCRIPT),
    )
    path = tmp_path / "huge.png"
    path.write_bytes(b"x" * (vision.MAX_INLINE_BYTES + 1))

    result = vision.extract_and_categorize(path)

    assert result.degraded.reason == "too_large"
    assert sent == [], "the size cap must fire before the call, not after"


def test_a_native_plus_vision_pdf_does_not_reuse_the_scan_classification(
    tmp_path, with_key, monkeypatch
):
    """A part-native PDF is stored as text layer + transcript combined.

    Vision only ever saw the scanned half, so its classification does not
    describe the document being stored. Classifying the combined text is worth
    the call.
    """
    monkeypatch.setattr(ocr_handler, "_tesseract_available", lambda: False)
    monkeypatch.setattr(
        vision, "_generate_combined",
        lambda data, mime_type: combined_vision_response(TRANSCRIPT),
    )
    path = tmp_path / "mixed.pdf"
    path.write_bytes(make_thin_text_pdf())

    result = file_parser.extract_text(path)

    assert result.method == "native+vision"
    assert result.categorization is None
