"""A shipped lookup table of embeddings for texts that never change.

**Why this exists.** Embedding is pure CPU, and the deploy target has very
little of it. `ONNXMiniLM_L6_V2` pads every input to a fixed 256 tokens
(`enable_padding(length=256)` in chromadb's tokenizer), so cost is a flat
per-text forward pass — there is nothing to shave by sending shorter strings.
Measured on the Phase 8 demo profile: 27 embeddings take **1.2s locally and
~50s on Render free**, whose 0.1 CPU share makes the same work ~40x slower.
That was the whole of "Load Demo Profile" feeling slow — the seed itself
(SQLite inserts, the idempotent clear) is ~0.2s.

The demo profile's texts are hand-authored module constants. Their vectors are
therefore knowable ahead of time, and shipping them turns those ~50s into a
dict lookup. `seed/precompute_vectors.py` generates the file; a `model`-marked
test asserts the shipped vectors still match what the live model produces.

**What is covered.** Both places the demo profile embeds:

* `embeddings.add_document` — one entry per `chunk_text` window, which also
  covers `ensure_synced()` re-indexing the demo after a free-tier restart wipes
  `data/chroma/`.
* `graph.builder.build_graph` — one entry per document's full `raw_text`, which
  it passes to `embeddings.query` for similarity edges on *every* graph read.

Nothing else is precomputed, and nothing else should be: a real upload's text is
unknown until it arrives, and a search query is unknown by definition. Those
still pay the model, as they must.

**Why hashing the text is the safety property.** Keys are the SHA-256 of the
exact string handed to `embed_texts`. Edit a demo document, retune `CHUNK_CHARS`,
or change the title prefix and every affected key simply stops matching, so the
text falls through to the real model and gets a correct vector. The failure mode
of a stale table is *slow*, never *wrong* — which is the only reason it is safe
to commit derived data at all. The model name is stored alongside and checked,
so swapping models discards the table wholesale rather than mixing vector
spaces.

Any problem reading the file yields an empty table: this is a cache, and a cache
must never be able to break embedding.
"""

from __future__ import annotations

import array
import base64
import hashlib
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

VECTORS_PATH = Path(__file__).resolve().parent / "precomputed_vectors.json"

# Little-endian float32, base64'd. ~40 KB for the demo profile against ~250 KB
# of JSON float literals, and it round-trips bit-exactly — `array('f').tolist()`
# yields the same Python floats as the ndarray's `.tolist()` in `embed_texts`,
# so a cache hit and a live embed are indistinguishable downstream.
ENCODING = "base64-float32-le"

_table: dict[str, list[float]] | None = None
_table_model: str | None = None


def key(text: str) -> str:
    """The table key for a text: SHA-256 of the exact string embed_texts sees."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decode(blob: str) -> list[float]:
    values = array.array("f")
    values.frombytes(base64.b64decode(blob))
    if sys.byteorder != "little":
        values.byteswap()
    return values.tolist()


def encode(vector: list[float]) -> str:
    """Inverse of `_decode`, for the generator script."""
    values = array.array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return base64.b64encode(values.tobytes()).decode("ascii")


def vectors_for(model_name: str) -> dict[str, list[float]]:
    """The shipped table, or `{}` if there is none usable for this model.

    Loaded once and cached in a module global. Returns `{}` rather than raising
    on a missing, corrupt, or wrong-model file — the caller then embeds for real,
    which is exactly the behaviour before this module existed.
    """
    global _table, _table_model
    if _table is not None and _table_model == model_name:
        return _table

    table: dict[str, list[float]] = {}
    try:
        raw = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        if raw.get("model") != model_name:
            logger.warning(
                "Precomputed vectors are for %s, not %s — ignoring them.",
                raw.get("model"),
                model_name,
            )
        elif raw.get("encoding") != ENCODING:
            logger.warning("Precomputed vectors use unknown encoding %r.", raw.get("encoding"))
        else:
            dim = raw.get("dim")
            for digest, blob in (raw.get("vectors") or {}).items():
                vector = _decode(blob)
                # A truncated entry would poison retrieval silently; drop it and
                # let that one text fall through to the model.
                if dim and len(vector) != dim:
                    logger.warning("Precomputed vector %s has wrong dimension.", digest[:12])
                    continue
                table[digest] = vector
    except FileNotFoundError:
        logger.debug("No precomputed vectors at %s.", VECTORS_PATH)
    except Exception:
        logger.exception("Could not read precomputed vectors — embedding live instead.")

    _table = table
    _table_model = model_name
    return table
