"""Query routing and the /api/search endpoint.

Two layers: `query_router.route` is unit-tested for the classification the demo
depends on (filter vs semantic), and the endpoint is tested end-to-end with
stubbed embeddings — dispatch, hydration from SQLite, the has_original flag, and
input validation.
"""

from __future__ import annotations

import pytest

from ai import query_router
from db import database
from tests.conftest import upload


# --- Router ---------------------------------------------------------------


@pytest.mark.parametrize(
    "query, category",
    [
        ("show all my certificates", "Certifications"),
        ("my certifications", "Certifications"),
        ("show internship documents", "Internships"),
        ("my AI projects", "Projects"),
        ("show my skills", "Skills"),
        ("my achievements", "Achievements"),
        ("show my academics", "Academics"),
        ("resume", "Academics"),  # alias: document-type word -> category
    ],
)
def test_router_detects_category_filters(query, category):
    result = query_router.route(query)
    assert result.mode == "filter"
    assert result.category == category


def test_router_latest_sets_sort_and_maps_alias():
    result = query_router.route("show my latest resume")
    assert result.mode == "filter"
    assert result.category == "Academics"
    assert result.sort == "latest"


@pytest.mark.parametrize(
    "query, category, document_type",
    [
        ("show my resume", "Academics", "resume"),
        ("my cv", "Academics", "resume"),
        ("show all my certificates", "Certifications", "certificate"),
        ("my certifications", "Certifications", "certificate"),
        ("show internship documents", "Internships", "internship_letter"),
        # Words that name a category but no document_type in the closed taxonomy.
        ("my marksheet", "Academics", None),
        ("show my projects", "Projects", None),
        ("my awards", "Achievements", None),
    ],
)
def test_router_carries_the_document_type_a_word_names(query, category, document_type):
    """A keyword resolves to a category *and* the type it names, where one exists.

    The category alone was a guess at what the model *should* have decided; the
    document_type is what the document is. Mutation check: dropping the
    document_type half of `_ALIASES` turns this red — and with it the resume and
    certificate regressions below.
    """
    result = query_router.route(query)
    assert result.mode == "filter"
    assert result.category == category
    assert result.document_type == document_type


def test_router_latest_without_category_is_still_a_filter():
    result = query_router.route("show my most recent documents")
    assert result.mode == "filter"
    assert result.category is None
    assert result.sort == "latest"


@pytest.mark.parametrize(
    "query",
    [
        "how does my Python certification connect to my internship?",
        "what did I learn during 2024",
        "which projects relate to my data science skills",
        "why is this relevant",
    ],
)
def test_router_questions_go_semantic_even_with_category_words(query):
    # These name categories ("certification", "projects", "skills") but are
    # questions — they want an answer, not a filtered list.
    assert query_router.route(query).mode == "semantic"


def test_router_unrecognized_query_is_semantic():
    assert query_router.route("tell me about my journey with python").mode == "semantic"


# --- Endpoint: filter mode ------------------------------------------------


def test_search_filter_returns_category_matches(client):
    # The stub categorizes every upload as Certifications.
    assert upload(client, "cert.txt", b"A certificate of completion").status_code == 200

    resp = client.post("/api/search", json={"query": "show all my certificates"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "filter"
    assert body["category"] == "Certifications"
    assert body["count"] == 1
    assert body["results"][0]["score"] is None  # exact match, not ranked


def test_search_filter_excludes_other_categories(client):
    """The structured filter itself is exact — it does not match another category.

    Everything stubs to Certifications, so an Internships filter matches nothing.
    Asserted at the router/SQL layer because the endpoint no longer *reports* an
    empty filter: it falls back to semantic search (see the fallback tests).
    """
    assert upload(client, "cert.txt", b"A certificate of completion").status_code == 200

    route = query_router.route("show my internships")
    rows = database.list_documents(
        user_id="demo", category=route.category, document_type=route.document_type
    )

    assert rows == []


# --- The word-vs-category mismatch (the real-document regression) ----------
#
# A word the user types names a document *type*; the category is Gemini's
# judgment, and the two disagree on real documents. Filtering on the predicted
# category alone hid the exact document the user asked for.


def _insert(doc_id: str, *, category: str, document_type: str, title: str) -> None:
    database.insert_document(
        doc_id=doc_id,
        user_id="demo",
        filename=f"{doc_id}.pdf",
        original_path="",
        file_type="text_entry",
        checksum="x" * 8,
        raw_text=f"{title} contents.",
        upload_date="2026-01-01T00:00:00",
        category=category,
        document_type=document_type,
        title=title,
    )


def test_filter_finds_a_document_the_model_filed_elsewhere(client):
    """The bug, exactly: a résumé Gemini filed under *Skills*.

    "show my resume" predicts Academics. Before the fix that filter excluded the
    one document the query names. Mutation check: revert list_documents to
    `AND category = ?` and this turns red.
    """
    _insert("r1", category="Skills", document_type="resume", title="Dayanand Kori Resume")

    body = client.post("/api/search", json={"query": "show my resume"}).json()

    assert body["mode"] == "filter"  # not a fallback — the filter itself matched
    assert [r["title"] for r in body["results"]] == ["Dayanand Kori Resume"]


def test_filter_still_matches_on_category_when_the_type_differs(client):
    """The other half of the OR: a marksheet is Academics but typed 'other'."""
    _insert("m1", category="Academics", document_type="other", title="Semester 3 Marksheet")

    body = client.post("/api/search", json={"query": "show my resume"}).json()

    assert [r["title"] for r in body["results"]] == ["Semester 3 Marksheet"]


def test_certificates_finds_a_certificate_filed_under_achievements(client):
    """plan.md §16's must-work query, against the seed's own shape.

    The Hackathon Winner Certificate is `document_type=certificate` in category
    *Achievements*; "show all my certificates" missed it before the fix.
    """
    _insert("c1", category="Certifications", document_type="certificate", title="Coursera Python")
    _insert("c2", category="Achievements", document_type="certificate", title="Hackathon Winner")

    body = client.post("/api/search", json={"query": "show all my certificates"}).json()

    assert {r["title"] for r in body["results"]} == {"Coursera Python", "Hackathon Winner"}


def test_documents_listing_is_still_an_exact_category_filter(client):
    """`GET /api/documents?category=` must not inherit search's OR widening.

    The timeline's chips filter on category and mean it — a résumé in *Skills*
    belongs under the Skills chip, not the Academics one.
    """
    _insert("r2", category="Skills", document_type="resume", title="A Resume")

    listed = client.get("/api/documents?category=Academics").json()

    assert listed == []
    assert [d["title"] for d in client.get("/api/documents?category=Skills").json()] == ["A Resume"]


# --- Endpoint: the empty-filter fallback -----------------------------------


def test_empty_filter_falls_back_to_semantic_and_says_so(client):
    """A filter that matches nothing must not report an empty library.

    The documents are there and embedded; only the router's word→category guess
    missed. Mutation check: delete the `if not results` fallback and this reddens.
    """
    assert upload(client, "cert.txt", b"A certificate of completion").status_code == 200

    body = client.post("/api/search", json={"query": "show my internships"}).json()

    assert body["count"] == 1
    # Reported honestly — these hits are related, not exact.
    assert body["mode"] == "semantic"
    assert body["fell_back"] is True
    assert body["category"] is None


def test_a_filter_that_matched_does_not_fall_back(client):
    assert upload(client, "cert.txt", b"A certificate of completion").status_code == 200

    body = client.post("/api/search", json={"query": "show all my certificates"}).json()

    assert body["mode"] == "filter"
    assert body["fell_back"] is False


def test_fallback_that_also_finds_nothing_stays_an_honest_empty(client):
    """With no documents at all, there is nothing to fall back to — say so
    plainly rather than claiming a semantic search happened."""
    body = client.post("/api/search", json={"query": "show my internships"}).json()

    assert body["count"] == 0
    assert body["mode"] == "filter"
    assert body["fell_back"] is False


# --- Endpoint: semantic mode ----------------------------------------------


def test_search_semantic_returns_ranked_hydrated_results(client):
    assert upload(client, "doc.txt", b"A document about machine learning").status_code == 200

    resp = client.post("/api/search", json={"query": "tell me about my ML work"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["count"] == 1
    hit = body["results"][0]
    assert hit["score"] is not None  # ranked
    assert hit["title"] == "Test Certificate"  # hydrated from SQLite
    assert hit["has_original"] is True  # a file was uploaded


def test_search_fileless_document_has_no_original(client):
    resp = client.post(
        "/api/ingest-text",
        json={"text": "Led the Data Science Club in 2024 and ran five workshops."},
    )
    assert resp.status_code == 200

    resp = client.post("/api/search", json={"query": "tell me about club leadership"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "semantic"
    assert body["count"] == 1
    assert body["results"][0]["has_original"] is False  # text_entry, no file


# --- Endpoint: validation -------------------------------------------------


def test_search_empty_query_is_rejected(client):
    assert client.post("/api/search", json={"query": "   "}).status_code == 400


def test_search_overlong_query_is_rejected(client):
    assert client.post("/api/search", json={"query": "x" * 501}).status_code == 400
