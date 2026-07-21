"""Layer A/B edge construction (plan.md §4 Module 3). Pure functions, no DB."""

from __future__ import annotations

from ai import relationship_engine as re


# --- Layer A: entity edges -------------------------------------------------


def test_shared_skill_becomes_one_node_linking_both_documents():
    """The money interaction: one skill hub, every carrier connected to it."""
    docs = [
        {"id": "cert", "category": "Certifications", "skills": ["Python"]},
        {"id": "proj", "category": "Projects", "skills": ["Python"]},
    ]
    nodes, edges = re.entity_edges(docs)

    assert [n["id"] for n in nodes] == ["skill:python"]
    assert {e["source"] for e in edges} == {"cert", "proj"}
    assert all(e["target"] == "skill:python" for e in edges)


def test_edge_relation_is_typed_by_document_category():
    docs = [
        {"id": "cert", "category": "Certifications", "skills": ["Python"]},
        {"id": "proj", "category": "Projects", "skills": ["Python"]},
    ]
    _, edges = re.entity_edges(docs)
    by_source = {e["source"]: e["relation_type"] for e in edges}

    assert by_source["cert"] == "certifies_skill"
    assert by_source["proj"] == "skill_used_in"


def test_skill_ids_are_case_and_space_insensitive():
    docs = [
        {"id": "a", "category": "Projects", "skills": ["Python"]},
        {"id": "b", "category": "Projects", "skills": [" python "]},
        {"id": "c", "category": "Projects", "skills": ["PYTHON"]},
    ]
    nodes, edges = re.entity_edges(docs)

    assert len(nodes) == 1, "one skill node, not three faint duplicates"
    assert len(edges) == 3


def test_a_skill_listed_twice_links_once():
    docs = [{"id": "a", "category": "Projects", "skills": ["Python", "python"]}]
    _, edges = re.entity_edges(docs)
    assert len(edges) == 1


def test_blank_skills_are_ignored():
    docs = [{"id": "a", "category": "Projects", "skills": ["", "  ", None]}]
    nodes, edges = re.entity_edges(docs)
    assert nodes == [] and edges == []


# --- Layer B: similarity edges ---------------------------------------------


def _neighbors(mapping):
    """Build a neighbors_of() from a {doc_id: [(other_id, score), ...]} map."""
    def _of(doc):
        return [{"doc_id": t, "score": s} for t, s in mapping.get(doc["id"], [])]
    return _of


def test_similar_pair_makes_one_undirected_edge():
    docs = [{"id": "x"}, {"id": "y"}]
    neighbors = _neighbors({"x": [("y", 0.9)], "y": [("x", 0.9)]})

    edges = re.similarity_edges(docs, neighbors)

    assert len(edges) == 1
    assert {edges[0]["source"], edges[0]["target"]} == {"x", "y"}
    assert edges[0]["relation_type"] == "similar_to"


def test_below_threshold_is_dropped():
    docs = [{"id": "x"}, {"id": "y"}]
    neighbors = _neighbors({"x": [("y", 0.5)]})
    assert re.similarity_edges(docs, neighbors) == []


def test_self_and_unknown_targets_are_dropped():
    docs = [{"id": "x"}, {"id": "y"}]
    # A self-hit and a hit on a document not in the set.
    neighbors = _neighbors({"x": [("x", 0.99), ("ghost", 0.99)]})
    assert re.similarity_edges(docs, neighbors) == []


def test_strongest_score_wins_for_a_pair():
    docs = [{"id": "x"}, {"id": "y"}]
    neighbors = _neighbors({"x": [("y", 0.80)], "y": [("x", 0.95)]})

    edges = re.similarity_edges(docs, neighbors)
    assert len(edges) == 1
    assert edges[0]["weight"] == 0.95
