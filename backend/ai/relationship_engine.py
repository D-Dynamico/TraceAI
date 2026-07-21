"""Deterministic relationship edges for the knowledge graph (plan.md §4 Module 3).

Two of Module 3's three layers live here; neither calls Gemini:

  - **Layer A — entity linking.** Documents that share a skill are connected
    through a single skill node. This is what powers the money interaction
    (plan.md §6 View 3): clicking the `Python` skill lights up every document
    that carries it — the certificate, the project, the internship. The edge is
    typed by the *document's* category, so a certificate `certifies_skill` while
    a project or internship `skill_used_in`.

  - **Layer B — embedding similarity.** Two documents whose vectors are closer
    than a threshold get a `similar_to` edge, surfacing non-obvious connections
    the entity layer misses. This reuses the existing semantic query (a document
    searched against the same store the search view uses) rather than a second
    vector API — the retrieval already computes cosine similarity.

Layer C (career-path inference) is a Gemini call and lives in `ai/career_path.py`.

Both functions here are pure given their inputs — dependencies are injected — so
they are exercised offline without a database or the embedding model.
"""

from __future__ import annotations

from typing import Any, Callable

# plan.md §4 Module 3 Layer B: link documents whose cosine similarity exceeds
# this. High enough that only genuinely related documents connect — a lower bar
# turns the graph into a hairball where every node touches every other.
SIMILARITY_THRESHOLD = 0.75

# The category whose skills are *certified* rather than merely *used*. Everything
# else that carries a skill is treated as having exercised it.
_CERTIFYING_CATEGORY = "Certifications"


def skill_node_id(value: str) -> str:
    """Stable node id for a skill, case- and space-insensitive.

    "Python", "python", and " Python " must collapse to one node or the money
    interaction fragments into three faint dots instead of one bright hub.
    """
    return "skill:" + " ".join(value.split()).lower()


def entity_edges(
    docs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Layer A: skill nodes plus the document→skill edges that connect them.

    `docs` is `[{id, category, skills: [...]}]`. Returns `(skill_nodes, edges)`.
    A skill node is emitted once per distinct skill (keyed by `skill_node_id`),
    its label taken from the first spelling seen. Edges are deduplicated per
    (document, skill) pair so a document listing a skill twice links once.
    """
    skill_nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    for doc in docs:
        doc_id = doc["id"]
        certifies = doc.get("category") == _CERTIFYING_CATEGORY
        for raw in doc.get("skills") or []:
            if not raw or not raw.strip():
                continue
            node_id = skill_node_id(raw)
            skill_nodes.setdefault(
                node_id, {"id": node_id, "type": "skill", "label": raw.strip()}
            )
            pair = (doc_id, node_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            edges.append(
                {
                    "source": doc_id,
                    "source_type": "document",
                    "target": node_id,
                    "target_type": "entity",
                    "relation_type": "certifies_skill" if certifies else "skill_used_in",
                    "weight": 1.0,
                }
            )

    return list(skill_nodes.values()), edges


def similarity_edges(
    docs: list[dict[str, Any]],
    neighbors_of: Callable[[dict[str, Any]], list[dict[str, Any]]],
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Layer B: `similar_to` edges between documents above the threshold.

    `docs` is `[{id, ...}]`; `neighbors_of(doc)` returns `[{doc_id, score}]` —
    the semantic neighbours of that document (injected so this is testable
    without the embedding model). Edges are undirected and emitted once per
    unordered pair, carrying the similarity as weight; self-matches are dropped.
    The strongest score wins when both directions of a pair clear the bar.
    """
    known = {doc["id"] for doc in docs}
    best: dict[tuple[str, str], float] = {}

    for doc in docs:
        source = doc["id"]
        for hit in neighbors_of(doc):
            target = hit.get("doc_id")
            score = hit.get("score")
            if target is None or score is None:
                continue
            if target == source or target not in known:
                continue
            if score < threshold:
                continue
            key = (source, target) if source < target else (target, source)
            if score > best.get(key, 0.0):
                best[key] = score

    return [
        {
            "source": a,
            "source_type": "document",
            "target": b,
            "target_type": "document",
            "relation_type": "similar_to",
            "weight": round(score, 4),
        }
        for (a, b), score in best.items()
    ]
