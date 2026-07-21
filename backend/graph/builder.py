"""Assemble the knowledge graph from SQLite + the vector store (plan.md §4 Module 3).

The graph is computed **on read**, not persisted: at a student-profile scale
(tens of documents) recomputing entity and similarity edges is instant and can
never go stale as documents are added. The one exception is career paths — a
Gemini call — which are persisted in their own table and merged in by
`ai/career_path.py`; this module builds the deterministic document/skill core.

Isolation: every source is scoped to `user_id` — `list_documents` and
`documents_with_skills` filter by it, and the similarity query is filtered by it
inside `embeddings.query`. A node for another user's document can therefore never
enter the graph. This is asserted and mutation-tested in `test_graph_api`.
"""

from __future__ import annotations

from typing import Any, Callable

from ai import relationship_engine
from db import database

# How many semantic neighbours to consider per document when drawing similarity
# edges. Small: only the closest handful can plausibly clear the 0.75 threshold,
# and a wide pool just does work the threshold throws away.
_SIMILARITY_K = 6


def _document_node(doc: dict[str, Any]) -> dict[str, Any]:
    """A document node carries what the detail panel shows on click."""
    return {
        "id": doc["id"],
        "type": "document",
        "label": doc.get("title") or doc.get("filename") or "Untitled",
        "category": doc.get("category"),
        "document_type": doc.get("document_type"),
        "file_type": doc.get("file_type"),
        "source_url": doc.get("source_url"),
        "effective_date": doc.get("effective_date"),
        "date_source": doc.get("date_source", "assumed"),
        # Same authoritative flag the timeline/search use — download vs open.
        "has_original": bool(doc.get("original_path")),
        "summary": doc.get("summary"),
    }


def build_graph(
    user_id: str = "demo",
    *,
    query_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build `{nodes, edges}` for one user's documents and skills.

    `query_fn` is the semantic-neighbour lookup, injected for testing; it
    defaults to the real `embeddings.query`. The document/skill core is built
    here; career-path nodes and their `leads_to` edges are merged in by the
    career-path module, which owns the Gemini call that produces them.
    """
    if query_fn is None:
        from ai import embeddings

        query_fn = embeddings.query

    rich = database.list_documents(user_id=user_id, limit=500)
    with_skills = database.documents_with_skills(user_id=user_id)
    text_by_id = {d["id"]: d.get("raw_text") or "" for d in with_skills}
    skills_by_id = {d["id"]: d.get("skills") or [] for d in with_skills}

    document_nodes = [_document_node(d) for d in rich]

    skill_nodes, entity_edges = relationship_engine.entity_edges(
        [{"id": d["id"], "category": d.get("category"), "skills": skills_by_id.get(d["id"], [])}
         for d in rich]
    )

    def neighbors_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
        text = text_by_id.get(doc["id"], "")
        if not text.strip():
            return []
        return query_fn(text, user_id=user_id, k=_SIMILARITY_K)

    similarity_edges = relationship_engine.similarity_edges(rich, neighbors_of)

    # Layer C: merge in the persisted, Gemini-inferred career paths and their
    # leads_to edges. An evidence id that no longer maps to one of this user's
    # documents is dropped — the supporting document may have been deleted since
    # inference ran, and a leads_to edge to nothing would dangle.
    known_ids = {d["id"] for d in rich}
    career_nodes, career_edges = _career_nodes_edges(known_ids)

    return {
        "nodes": document_nodes + skill_nodes + career_nodes,
        "edges": entity_edges + similarity_edges + career_edges,
    }


def _career_nodes_edges(
    known_doc_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for path in database.list_career_paths():
        nodes.append(
            {
                "id": path["id"],
                "type": "career_path",
                "label": path["title"],
                "match_score": path.get("match_score"),
                "skill_gaps": ", ".join(path.get("skill_gaps") or []) or None,
            }
        )
        for doc_id in path.get("evidence_doc_ids") or []:
            if doc_id not in known_doc_ids:
                continue
            edges.append(
                {
                    "source": doc_id,
                    "source_type": "document",
                    "target": path["id"],
                    "target_type": "career_path",
                    "relation_type": "leads_to",
                    "weight": path.get("match_score") or 1.0,
                }
            )
    return nodes, edges
