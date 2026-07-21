"""Query routing and the /api/search endpoint.

Two layers: `query_router.route` is unit-tested for the classification the demo
depends on (filter vs semantic), and the endpoint is tested end-to-end with
stubbed embeddings — dispatch, hydration from SQLite, the has_original flag, and
input validation.
"""

from __future__ import annotations

import pytest

from ai import query_router
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
    assert upload(client, "cert.txt", b"A certificate of completion").status_code == 200
    # Everything stubs to Certifications, so an Internships filter finds nothing.
    resp = client.post("/api/search", json={"query": "show my internships"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


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
