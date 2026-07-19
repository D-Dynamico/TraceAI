"""Shared fixtures.

Two things every test gets for free:

  - **Isolated storage.** `settings` is a module-level singleton that every
    module imports by reference, so repointing its paths at a tmp directory
    redirects the whole app. Without this, running the suite would write real
    files into `uploads/` and rows into `data/traceai.db`.

  - **A stubbed categorizer.** Tests must not spend Gemini quota or fail when
    the network is down. `categorize()` is replaced with a deterministic stub;
    tests that want the real API opt in via the `live` marker.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import storage
from ai import categorizer
from config import settings
from db import database
from models.document import Categorization

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Point uploads and the database at a per-test tmp directory."""
    uploads = tmp_path / "uploads"
    data = tmp_path / "data"
    uploads.mkdir()
    data.mkdir()

    monkeypatch.setattr(settings, "upload_dir", uploads)
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "db_path", data / "traceai.db")

    database.init_db()
    yield tmp_path


@pytest.fixture
def stub_result() -> Categorization:
    """The categorization every stubbed upload returns."""
    return Categorization(
        document_type="certificate",
        category="Certifications",
        title="Test Certificate",
        date="2024-03",
        summary="A stubbed summary.",
        skills=["Python", "SQL"],
        organizations=["Coursera"],
        people=["Ada Lovelace"],
        tags=["testing"],
        confidence=0.9,
    )


@pytest.fixture(autouse=True)
def stub_categorizer(request, monkeypatch, stub_result):
    """Replace the Gemini call with a deterministic stub.

    Opted out of by two markers:
      - `live`   — exercises the real API on purpose.
      - `nostub` — exercises the real `categorize()` logic offline (its
        fallback and error handling). Without this escape hatch such tests
        would silently assert against the stub and pass for the wrong reason.
    """
    if request.node.get_closest_marker("live") or request.node.get_closest_marker("nostub"):
        return

    def _fake(text: str, filename: str = "") -> Categorization:
        return stub_result.model_copy(deep=True)

    # Patched where it is looked up, not just where it is defined.
    monkeypatch.setattr(categorizer, "categorize", _fake)
    import routes.upload as upload_route

    monkeypatch.setattr(upload_route.categorizer, "categorize", _fake)


@pytest.fixture
def client() -> TestClient:
    from main import app

    return TestClient(app)


# --- Sample documents ------------------------------------------------------


def make_docx(
    heading: str = "Internship Completion Certificate",
    body: str = "This certifies completion of a Python internship at Acme AI.",
) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(heading, level=1)
    doc.add_paragraph(body)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def make_pptx(title: str = "ML Pipeline Project") -> bytes:
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = title
    slide.placeholders[1].text = "Built an ML pipeline using scikit-learn."
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def upload(client: TestClient, name: str, data: bytes, mime: str = "text/plain"):
    return client.post("/api/upload", files={"file": (name, data, mime)})


@pytest.fixture
def stored_doc(client):
    """Upload a docx and return (doc_id, original_bytes, response_json)."""
    original = make_docx()
    resp = upload(client, "cert.docx", original, DOCX_MIME)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"], original, resp.json()
