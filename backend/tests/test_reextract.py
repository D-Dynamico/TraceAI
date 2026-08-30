"""`POST /api/documents/{id}/reextract` — the cure for a terminal extraction.

Extraction sits upstream of everything, so losing it loses the document: with
`raw_text` empty the categorizer degrades to a filename guess, the embedding
carries no signal, and `/recategorize` — the existing retry — re-runs the model
over that same empty string and changes nothing. Before this route the only fix
was delete-and-reupload.

**Why it is not a niche path.** The free tier is 5 RPM and *20 requests per
day*, and a scanned upload spends 2 of them. Running out mid-session is the
normal case, not the pathological one, and every document uploaded after the
wall is hit lands with no text at all.

Everything here runs offline: `conftest.stub_vision` replaces `vision._generate`
(the only network touch) and defaults to an empty transcript, i.e. "the model
saw nothing" — which is exactly the failed-extraction state these tests start
from. Tests that need a *successful* re-extraction patch it themselves.
"""

from __future__ import annotations

import uuid

import pytest

from ai import categorizer, vision
from conftest import combined_vision_response, make_image, upload
from config import settings
from db import database
import storage

TRANSCRIPT = """CERTIFICATE OF COMPLETION
Awarded to Dayanand for completing Deep Learning Specialization.
Issued by Coursera, March 2024.
"""


@pytest.fixture(autouse=True)
def deterministic_key(monkeypatch):
    """Pin the key so the Vision rung is entered on every machine.

    Without this the outcome depends on whether the developer's `.env` happens
    to hold a key: with one, a failed extraction degrades to `no_text`; without,
    it short-circuits at `no_api_key`. Tests asserting on a reason code would
    then pass here and fail in CI, or vice versa.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "fake-key-for-tests")


@pytest.fixture
def vision_recovers(monkeypatch):
    """Make the next Vision call succeed — the quota-has-refilled case."""
    monkeypatch.setattr(
        vision, "_generate_combined",
        lambda data, mime_type: combined_vision_response(TRANSCRIPT),
    )


@pytest.fixture
def categorize_calls(monkeypatch):
    """Count categorization calls. Each one is 5% of the day's quota."""
    calls: list[str] = []

    def _fake(text: str, filename: str = ""):
        calls.append(text)
        from models.document import Categorization

        return Categorization(
            document_type="certificate",
            category="Certifications",
            title="Deep Learning Specialization",
            date="2024-03",
            summary="Recovered by re-extraction.",
            skills=["Deep Learning"],
            confidence=0.9,
        )

    monkeypatch.setattr(categorizer, "categorize", _fake)
    import routes.upload as upload_route

    monkeypatch.setattr(upload_route.categorizer, "categorize", _fake)
    return calls


@pytest.fixture
def failed_scan(client):
    """An image uploaded while Vision was down: stored, but with no text.

    This is the state the endpoint exists to repair, produced the honest way —
    through the real `/upload` path with the default empty-transcript stub —
    rather than by hand-writing a row that only resembles it.
    """
    resp = upload(client, "scan.png", make_image(), "image/png")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["char_count"] == 0, "fixture must start from a failed extraction"
    return body["id"], body


# --- The structured reason (previously prose only) --------------------------


def test_upload_reports_why_extraction_produced_nothing(failed_scan):
    """A reason code, not just a sentence in `warnings`."""
    _, body = failed_scan

    assert body["extraction_degraded_reason"] == "no_text"
    assert body["extraction_retryable"] is False
    assert any("No text could be extracted" in w for w in body["warnings"])


def test_a_quota_wall_is_reported_as_retryable(client, monkeypatch):
    """The distinction the prose could not carry: this one clears itself."""

    def _quota_exhausted(data, mime_type):
        raise RuntimeError("429 ResourceExhausted: quota exceeded")

    monkeypatch.setattr(vision, "_generate_combined", _quota_exhausted)

    body = upload(client, "scan.png", make_image(), "image/png").json()

    assert body["extraction_degraded_reason"] == "quota"
    assert body["extraction_retryable"] is True


def test_the_reason_is_persisted_for_a_later_reader(client, failed_scan):
    """The UI decides whether to offer a retry from the stored row, not from
    the upload response it has long since discarded."""
    doc_id, _ = failed_scan

    meta = client.get(f"/api/documents/{doc_id}").json()["metadata"]

    assert meta["extraction_degraded_reason"] == "no_text"
    assert meta["extraction_retryable"] is False


def test_a_successful_extraction_carries_no_reason(client):
    body = upload(client, "notes.txt", b"Plain text extracts natively.").json()

    assert body["extraction_degraded_reason"] is None
    assert body["extraction_retryable"] is False


# --- Recovery ---------------------------------------------------------------


def test_reextract_recovers_text_the_upload_lost(
    client, failed_scan, vision_recovers, categorize_calls
):
    doc_id, _ = failed_scan

    resp = client.post(f"/api/documents/{doc_id}/reextract")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recovered"] is True
    assert body["method"] == "vision"
    assert "CERTIFICATE" in body["text_preview"]

    row = database.get_document(doc_id)
    assert "Deep Learning Specialization" in row["raw_text"]
    assert row["metadata"]["extraction_degraded_reason"] is None, (
        "a cleared failure must not leave the UI offering a retry forever"
    )


def test_recovered_text_is_reclassified(client, failed_scan, vision_recovers, categorize_calls):
    """Text without fresh metadata is still broken — the row would keep the
    filename guess the failed upload produced.

    The classification now comes back *with* the transcript, from the same
    Vision call, so `categorize_calls` staying empty is the assertion that
    matters here: recovery costs one Gemini request instead of two, on the one
    path that exists because quota ran out in the first place.
    """
    doc_id, _ = failed_scan

    body = client.post(f"/api/documents/{doc_id}/reextract").json()

    assert body["recategorized"] is True
    assert body["categorization"]["title"] == "Scanned Certificate"
    assert categorize_calls == [], (
        "a second call to re-derive what the Vision response already carried"
    )

    row = database.get_document(doc_id)
    assert row["category"] == "Certifications"
    assert row["confidence"] == 0.8
    assert row["title"] == "Scanned Certificate"


def test_recovered_text_becomes_searchable(client, failed_scan, vision_recovers, categorize_calls):
    """The whole point: a scan that was invisible to search can now be found.

    plan.md §15 scores retrieval at 40%, and an unindexed document is not
    retrievable at any quality.
    """
    doc_id, _ = failed_scan

    client.post(f"/api/documents/{doc_id}/reextract")

    assert database.get_document(doc_id)["embedding_id"] == doc_id


def test_the_original_is_re_read_never_rewritten(
    client, failed_scan, vision_recovers, categorize_calls
):
    """Re-extraction derives from the original; it must not touch it."""
    doc_id, _ = failed_scan
    stored_path, before = storage.find_by_id(doc_id, "demo")
    original_bytes = stored_path.read_bytes()

    client.post(f"/api/documents/{doc_id}/reextract")

    after = storage.load_manifest(stored_path)
    assert stored_path.read_bytes() == original_bytes, "original bytes moved"
    assert after.checksum == before.checksum, "the integrity record must survive"
    assert after.upload_date == before.upload_date
    # The sidecar's *derived* half is expected to move — that is where anything
    # re-derived belongs (CLAUDE.md).
    assert "CERTIFICATE" in after.extraction["text"]
    assert after.extraction["reextracted_at"]
    assert client.get(f"/api/documents/{doc_id}/verify").json()["verified"] is True


# --- Failure paths: the endpoint must never make things worse ---------------


def test_a_failed_reextract_keeps_the_text_it_already_had(client, categorize_calls):
    """A retry that fails must not overwrite good text with an empty string.

    The realistic sequence: a scan is recovered on Monday, someone clicks retry
    on Tuesday after the quota is spent. Nothing recovered — and the document
    must come out exactly as it went in.
    """
    doc_id, _ = _seed_recovered_scan(client)

    body = client.post(f"/api/documents/{doc_id}/reextract").json()

    assert body["recovered"] is False
    assert body["extraction_degraded_reason"] == "no_text"
    row = database.get_document(doc_id)
    assert "CERTIFICATE" in row["raw_text"], "recovered text was destroyed"


def test_a_failed_reextract_spends_no_categorization_call(
    client, failed_scan, categorize_calls
):
    """20 requests a day — classifying an empty string is a call for nothing."""
    doc_id, _ = failed_scan

    body = client.post(f"/api/documents/{doc_id}/reextract").json()

    assert body["recovered"] is False
    assert body["recategorized"] is False
    assert categorize_calls == []


def test_a_failed_reextract_records_this_attempts_reason(client, failed_scan, monkeypatch):
    """Not the stale one from upload — the two have opposite fixes."""
    doc_id, _ = failed_scan

    def _quota_exhausted(data, mime_type):
        raise RuntimeError("429 ResourceExhausted: quota exceeded")

    monkeypatch.setattr(vision, "_generate_combined", _quota_exhausted)

    body = client.post(f"/api/documents/{doc_id}/reextract").json()

    assert body["extraction_degraded_reason"] == "quota"
    assert body["extraction_retryable"] is True
    assert database.get_document(doc_id)["metadata"]["extraction_degraded_reason"] == "quota"


def test_reextract_refuses_a_corrupted_original(client, failed_scan, vision_recovers):
    """Same rule as download: never derive from a file that failed its checksum."""
    doc_id, _ = failed_scan
    stored_path, _ = storage.find_by_id(doc_id, "demo")
    stored_path.write_bytes(b"not the file that was uploaded")

    resp = client.post(f"/api/documents/{doc_id}/reextract")

    assert resp.status_code == 500
    assert "integrity" in resp.json()["detail"].lower()
    assert database.get_document(doc_id)["raw_text"] == ""


def test_reextract_skips_the_call_when_nothing_would_change(
    client, stored_doc, categorize_calls
):
    """A healthy document re-extracts to the same text it already had.

    Re-classifying it would spend a call to reproduce the answer already stored.
    """
    doc_id, _, _ = stored_doc

    body = client.post(f"/api/documents/{doc_id}/reextract").json()

    assert body["recovered"] is True
    assert body["recategorized"] is False
    assert categorize_calls == []


def test_reextract_409_for_a_document_with_no_original(client):
    """A text entry is fine — it simply has nothing to re-extract from."""
    doc_id = client.post(
        "/api/ingest-text",
        json={"text": "Led the Data Science Club in 2024, ran five workshops."},
    ).json()["id"]

    resp = client.post(f"/api/documents/{doc_id}/reextract")

    assert resp.status_code == 409
    assert "no stored original" in resp.json()["detail"].lower()


def test_reextract_404_for_unknown_id(client):
    assert client.post("/api/documents/" + "0" * 32 + "/reextract").status_code == 404


def test_reextract_409_when_the_original_is_gone(client, failed_scan):
    """The row survives a missing file, so say which is missing."""
    doc_id, _ = failed_scan
    stored_path, _ = storage.find_by_id(doc_id, "demo")
    stored_path.unlink()

    resp = client.post(f"/api/documents/{doc_id}/reextract")

    assert resp.status_code == 409
    assert "no longer on disk" in resp.json()["detail"].lower()


# --- Helpers ----------------------------------------------------------------


def _seed_recovered_scan(client):
    """A scan whose text was recovered by an earlier run, with Vision now down.

    Uploads with Vision working, then lets the autouse stub take over again for
    the re-extraction — so the "second attempt fails" state is reached through
    the real code path rather than asserted about a fabricated row.
    """
    import routes.upload as upload_route

    original = vision._generate_combined
    vision._generate_combined = lambda data, mime_type: combined_vision_response(TRANSCRIPT)
    try:
        body = upload(client, "scan.png", make_image(), "image/png").json()
    finally:
        vision._generate_combined = original

    assert "CERTIFICATE" in database.get_document(body["id"])["raw_text"]
    return body["id"], body
