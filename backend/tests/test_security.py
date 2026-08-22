"""Regression tests for previously-found security issues.

Each test here corresponds to a real defect, not a hypothetical one. They are
grouped so the reason they exist stays visible.
"""

from __future__ import annotations

import pytest

import storage
from ai import categorizer
from conftest import upload


class TestGlobInjection:
    """doc_id reaches find_by_id() from the URL and is used in a glob pattern.

    Before this was fixed, `GET /api/documents/*/download` returned the first
    stored file — retrieving someone's document without knowing any id.
    """

    @pytest.mark.parametrize(
        "probe",
        ["*", "**/*", "?" * 32, "[a-z]" * 8, "../../../etc/passwd", "../..", "",
         "ABC" * 11, "0" * 31, "0" * 33],
        ids=["star", "globstar", "question-marks", "char-class", "traversal",
             "dotdot", "empty", "non-hex", "too-short", "too-long"],
    )
    def test_malformed_ids_are_rejected(self, probe, stored_doc):
        # stored_doc is required: against an empty upload directory every
        # lookup returns None anyway, and this test would pass with the guard
        # removed entirely.
        assert storage.find_by_id(probe, "demo") is None

    def test_wildcard_download_is_404_over_http(self, client, stored_doc):
        """A real file exists, so a successful glob would return 200."""
        assert client.get("/api/documents/*/download").status_code == 404
        assert client.get("/api/documents/*/verify").status_code == 404

    def test_valid_ids_still_resolve(self, client, stored_doc):
        doc_id, _, _ = stored_doc

        assert storage.find_by_id(doc_id, "demo") is not None
        assert client.get(f"/api/documents/{doc_id}/download").status_code == 200

    def test_uppercase_hex_is_rejected(self, client, stored_doc):
        """uuid4().hex is lowercase; accepting other casings widens the pattern."""
        doc_id, _, _ = stored_doc

        assert storage.find_by_id(doc_id.upper(), "demo") is None


class TestApiKeyLeakage:
    """SDK errors can carry a request URL containing ?key=<api key>.

    Logs get pasted into issues and CI output, so the key is scrubbed rather
    than trusting every error path not to include it.
    """

    def test_redacts_the_configured_key(self, monkeypatch):
        monkeypatch.setattr(categorizer.settings, "gemini_api_key", "SUPERSECRET12345")

        out = categorizer._redact("POST https://api/v1?key=SUPERSECRET12345 failed 403")

        assert "SUPERSECRET12345" not in out
        assert "REDACTED" in out

    def test_redacts_a_key_query_param_it_has_never_seen(self, monkeypatch):
        monkeypatch.setattr(categorizer.settings, "gemini_api_key", "")

        out = categorizer._redact("url?key=SomeOtherKeyValue999 denied")

        assert "SomeOtherKeyValue999" not in out

    @pytest.mark.nostub
    def test_failure_logs_do_not_contain_the_key(self, monkeypatch, caplog):
        """End-to-end: an SDK error carrying the key must not reach the log.

        Needs the real categorize(); against the stub this would pass without
        ever producing a log line.
        """
        key = "LEAKYKEY_ABCDEFGHIJ"
        monkeypatch.setattr(categorizer.settings, "gemini_api_key", key)
        monkeypatch.setattr(
            categorizer, "_rate_limiter", type("N", (), {"wait": lambda s: None})()
        )

        class _Exploding:
            def generate_content(self, prompt):
                raise RuntimeError(f"GET https://x/v1?key={key} returned 400")

        monkeypatch.setattr(categorizer, "_get_model", lambda: _Exploding())

        with caplog.at_level("DEBUG"):
            categorizer.categorize("Some document text to classify.", "cert.pdf")

        assert key not in caplog.text


class TestErrorMessageDisclosure:
    def test_index_failure_does_not_echo_internals(self, client, monkeypatch):
        """The DB-failure path returned the raw exception, exposing local paths."""
        def _boom(**kwargs):
            raise RuntimeError(r"no such table: documents at C:\Users\someone\data.db")

        import routes.upload as upload_route

        monkeypatch.setattr(upload_route.database, "insert_document", _boom)

        resp = upload(client, "notes.txt", b"Some achievement text here.")

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "C:\\Users" not in detail
        assert "no such table" not in detail


class TestSqlInjection:
    def test_category_filter_is_parameterized(self, client, stored_doc):
        """A crafted category must be treated as a value, not SQL."""
        resp = client.get("/api/documents", params={"category": "x'; DROP TABLE documents;--"})

        assert resp.status_code == 200
        assert resp.json() == []
        # The table must still be there.
        assert client.get("/api/documents").status_code == 200
        assert len(client.get("/api/documents").json()) == 1


class TestErrorsDoNotLeakInternals:
    """A 500 must not hand the client a path from inside the container.

    Three routes echoed `str(exc)` back — an IOError's message is the stored
    path, and an extraction failure's often is too. They are logged in full;
    the response says what happened and nothing about where.
    """

    def test_a_storage_failure_reports_no_path(self, client, monkeypatch):
        from routes import upload as upload_route

        def _boom(user_id, doc_id, filename, contents):
            raise IOError(r"[Errno 28] No space left: '/srv/traceai/uploads/demo/x.txt'")

        monkeypatch.setattr(upload_route.storage, "save_original", _boom)

        resp = client.post(
            "/api/upload", files={"file": ("notes.txt", b"some text", "text/plain")}
        )

        assert resp.status_code == 500
        assert "/srv/traceai" not in resp.text
        assert "Errno" not in resp.text

    def test_an_extraction_failure_reports_no_path(self, client, monkeypatch):
        from routes import upload as upload_route

        def _boom(path):
            raise RuntimeError(f"parser died reading {path}")

        monkeypatch.setattr(upload_route.file_parser, "extract_text", _boom)

        resp = client.post(
            "/api/upload", files={"file": ("notes.txt", b"some text", "text/plain")}
        )

        assert resp.status_code == 422
        assert "uploads" not in resp.text.lower()

    def test_an_unhandled_error_returns_the_degradation_envelope(self, monkeypatch):
        """Anything a route did not catch still answers in a shape a client can read.

        Built with `raise_server_exceptions=False` because TestClient's default
        is to re-raise a 500 into the test instead of returning it — which is
        useful everywhere else and hides exactly the response under test here.
        Starlette still re-raises after the handler has produced the response, so
        this is what a browser receives.
        """
        from fastapi.testclient import TestClient

        from main import app
        from routes import documents as documents_route

        def _boom(*args, **kwargs):
            raise RuntimeError("internal detail nobody outside should see")

        monkeypatch.setattr(documents_route.database, "list_documents", _boom)

        resp = TestClient(app, raise_server_exceptions=False).get("/api/documents")

        assert resp.status_code == 500
        body = resp.json()
        assert body["reason"] == "internal_error"
        assert body["retryable"] is True
        assert "internal detail" not in resp.text
