"""Regenerate `backend/ai/precomputed_vectors.json` for the demo profile.

Run from the repo root after editing `DOCS`, or after changing `chunk_text`'s
windowing constants:

    PYTHONPATH=backend python -m seed.precompute_vectors

Loads the real ONNX model (~80 MB on first run) and embeds every text the demo
profile will ask for at runtime. Skipping this after an edit is not a
correctness problem — a changed text hashes differently, misses the table, and
gets embedded live — it just gives that document back its ~2s-per-embed cost on
the free instance. `backend/tests/test_precomputed.py` (marked `model`) fails
when the shipped file has drifted, so the omission is caught rather than
silently shipped.

See `backend/ai/precomputed.py` for why this exists at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_BACKEND = _ROOT / "backend"
for _path in (str(_BACKEND), str(_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from ai import embeddings, precomputed  # noqa: E402
from seed.seed_demo import DOCS  # noqa: E402


def demo_texts() -> list[str]:
    """Every string the demo profile hands to `embed_texts`, deduplicated.

    One call site: `embeddings.add_document` embeds each `chunk_text` window —
    what `load_demo` writes, and what `ensure_synced` re-writes after a free-tier
    restart wipes the Chroma store.

    **There used to be a second.** `graph.builder` passed each document's whole
    `raw_text` to `embeddings.query` on every graph read, so the table had to
    carry those strings too. The builder now looks neighbours up by document id
    against vectors the store already holds, embedding nothing — so those
    entries were dropped from the shipped table.
    """
    texts: list[str] = []
    for doc in DOCS:
        texts.extend(embeddings.chunk_text(doc["raw_text"], doc["title"]))

    seen: set[str] = set()
    return [t for t in texts if not (t in seen or seen.add(t))]


def build() -> dict[str, object]:
    texts = demo_texts()
    # Straight to the model, deliberately bypassing `embed_texts` — going
    # through it would serve hits from the very table being regenerated, so a
    # stale entry would copy itself forward instead of being replaced.
    model = embeddings._get_model()
    vectors = [v.tolist() for v in model(texts)]

    return {
        "model": embeddings.MODEL_NAME,
        "dim": len(vectors[0]),
        "encoding": precomputed.ENCODING,
        "generated_by": "seed/precompute_vectors.py",
        "vectors": {
            precomputed.key(text): precomputed.encode(vector)
            for text, vector in zip(texts, vectors)
        },
    }


if __name__ == "__main__":
    payload = build()
    precomputed.VECTORS_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    size_kb = precomputed.VECTORS_PATH.stat().st_size / 1024
    print(
        f"Wrote {len(payload['vectors'])} vectors "
        f"({payload['dim']}d, {size_kb:.0f} KB) to {precomputed.VECTORS_PATH}"
    )
