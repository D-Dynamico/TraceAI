"""The Original Format Preservation guarantee (plan.md section 1).

These are the tests that make the guarantee real rather than aspirational.
`test_tamper_is_detected` is the important one: it is the only thing proving
that a corrupted original is caught instead of served.
"""

from __future__ import annotations

import hashlib

from conftest import DOCX_MIME, make_docx, upload

import storage


def test_upload_checksum_matches_source_bytes(client):
    original = make_docx()
    expected = hashlib.sha256(original).hexdigest()

    resp = upload(client, "cert.docx", original, DOCX_MIME)

    assert resp.status_code == 200, resp.text
    assert resp.json()["checksum"] == expected
    assert resp.json()["size_bytes"] == len(original)


def test_verify_reports_intact_file(client, stored_doc):
    doc_id, _, _ = stored_doc

    body = client.get(f"/api/documents/{doc_id}/verify").json()

    assert body["verified"] is True


def test_download_is_byte_for_byte_identical(client, stored_doc):
    doc_id, original, meta = stored_doc

    resp = client.get(f"/api/documents/{doc_id}/download")

    assert resp.status_code == 200
    assert resp.content == original
    assert resp.headers.get("x-content-sha256") == meta["checksum"]


def test_extraction_does_not_modify_the_original(client, stored_doc):
    doc_id, original, _ = stored_doc

    stored_path, manifest = storage.find_by_id(doc_id, "demo")

    assert stored_path.read_bytes() == original
    assert manifest.extraction["char_count"] > 0, "expected text to have been extracted"


def test_extracted_text_lives_in_a_separate_file(client, stored_doc):
    doc_id, _, _ = stored_doc

    stored_path, _ = storage.find_by_id(doc_id, "demo")
    sidecar = storage.manifest_path_for(stored_path)

    assert sidecar.exists()
    assert sidecar != stored_path


def test_tamper_is_detected_and_download_refused(client, stored_doc):
    """Corrupt the stored original; verify must fail and download must refuse.

    Without this, nothing distinguishes a working checksum from a decorative one.
    """
    doc_id, _, _ = stored_doc
    stored_path, _ = storage.find_by_id(doc_id, "demo")

    stored_path.write_bytes(stored_path.read_bytes() + b"CORRUPTED")

    assert client.get(f"/api/documents/{doc_id}/verify").json()["verified"] is False

    resp = client.get(f"/api/documents/{doc_id}/download")
    assert resp.status_code == 500
    assert "integrity" in resp.json()["detail"].lower()


def test_unknown_id_returns_404(client):
    assert client.get("/api/documents/" + "0" * 32 + "/download").status_code == 404
