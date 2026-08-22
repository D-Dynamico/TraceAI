"""Local embedding generation + ChromaDB vector store (plan.md §4 Module 5).

The semantic half of search. Three things this module takes seriously:

1. **Embeddings are local and free.** `all-MiniLM-L6-v2` runs on CPU, so unlike
   the Gemini path in `categorizer.py` there is **no rate limiter** — do not add
   one. A batch upload embeds as fast as the CPU allows.

   It runs through **Chroma's bundled ONNX export**, not sentence-transformers,
   because the deploy target (Render free) caps an instance at 512 MB and torch
   does not fit: measured peak working set for this exact workload was **439 MB
   resident for sentence-transformers versus 212 MB for ONNX**, before FastAPI,
   Chroma, and PyMuPDF are counted. Same model, same weights — the two backends
   were verified to produce **identical** vectors (minimum per-chunk cosine
   1.0000) over the Phase 8 seed, yielding the same four `similar_to` edges at
   the 0.75 threshold with scores equal to three decimals. So this is a memory
   decision only; retrieval behaviour is unchanged.

2. **SQLite is the source of truth; Chroma is derived.** Every document's
   `raw_text` is preserved in SQLite, so a deleted or corrupt `data/chroma/` is
   fully rebuildable via `reindex()`. `ensure_synced()` does this automatically
   on startup — a wiped store heals, a partial one fills in.

3. **The model is under our control, not Chroma's.** Chroma can embed for us, but
   we always pass our own vectors (`embed_texts` is the single choke point) so
   the model is deterministic and tests can stub one function instead of faking a
   collection. The collection is created with **no** embedding function for that
   reason.

Every embedding flows through `embed_texts`; every write is keyed so that a
re-add replaces rather than duplicates. Failure to embed never loses a document —
the caller degrades and the document simply stays unindexed until the next sync.

That single choke point is also where `ai/precomputed.py` intercepts: texts whose
vectors ship with the repo (the demo profile's, which are constants) skip the
model entirely. Read that module before touching `embed_texts` — inference is
~40x slower on the free instance than locally, and the demo depends on avoiding
it. Nothing else is cached, and correctness does not depend on the table
existing.
"""

from __future__ import annotations

import logging
import shutil
import threading
from typing import Any, cast

from ai import precomputed
from config import settings

logger = logging.getLogger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "documents"

# all-MiniLM-L6-v2 truncates at ~256 tokens (~1k chars), so a single embedding of
# a long document would only "see" its head. Windowing keeps later content
# searchable; the overlap avoids a sentence being split across a boundary and
# lost from both windows.
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
# A pathological scrape (a whole blog archive) must not explode the store. This
# caps coverage at ~30k chars — well past any certificate or resume, and in line
# with the 20k the categorizer already truncates to.
MAX_CHUNKS = 40

# Pull more chunks than the document count we want back: several chunks can
# belong to one document, and we dedup to distinct documents afterward.
_QUERY_POOL_FACTOR = 5
_QUERY_POOL_MIN = 20

# How many of a document's own chunks to use as neighbour probes. Every extra
# probe is another HNSW search, and the head of a document already dominates its
# similarity to anything else; 8 covers ~7k characters, well past a certificate
# or a resume, at a fraction of the cost of probing all MAX_CHUNKS.
_NEIGHBOR_CHUNK_CAP = 8

# Embed in small batches rather than handing the backend all MAX_CHUNKS at once.
# The batch is padded to its longest member and held in memory as one tensor, so
# a single 40-chunk shot peaked at 500 MB against Render free's 512 MB ceiling;
# at 8 it peaks at 315 MB. Both measured. The cost is more forward passes on a
# document long enough to hit the cap, which is milliseconds on local CPU.
EMBED_BATCH = 8

_model = None
_model_lock = threading.Lock()
_client = None
_collection = None
_store_lock = threading.Lock()


# --- Embedding model ------------------------------------------------------


def _get_model():
    """Lazily load and cache the ONNX embedding function.

    Deferred so importing this module (and starting the app) does not trigger the
    ~80MB model download; it happens on the first real embed. That matters on a
    free instance, where a cold start with an empty store does no embedding at
    all and should not pay for the model. Tests stub `embed_texts`, so they never
    reach here.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

            logger.info("Loading embedding model %s (onnx)", MODEL_NAME)
            _model = ONNXMiniLM_L6_V2()
        return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts as normalized vectors, cosine-ready.

    The single point every embedding flows through — add, query, and reindex all
    call it, which is why tests stub exactly this one function. Vectors are
    L2-normalized (the ONNX embedder normalizes internally) so cosine distance in
    Chroma matches plan.md §4's cosine similarity.

    Texts with a shipped vector (`ai/precomputed.py` — the demo profile, whose
    text is fixed) are served from that table and never reach the model. Only the
    misses are embedded, so `_get_model()` is not called at all when every text
    hits: a demo-only session on the free instance never loads the model. Results
    are identical either way, so callers cannot tell which path a vector took.

    Misses are batched at EMBED_BATCH to bound peak memory; see that constant.
    """
    if not texts:
        return []

    table = precomputed.vectors_for(MODEL_NAME)
    vectors: list[list[float] | None] = [table.get(precomputed.key(t)) for t in texts]
    pending = [i for i, v in enumerate(vectors) if v is None]

    if pending:
        model = _get_model()
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            for i, v in zip(batch, model([texts[i] for i in batch])):
                # float32 ndarray from onnxruntime -> plain floats for Chroma/JSON.
                vectors[i] = v.tolist()

    return cast("list[list[float]]", vectors)


# --- Chunking -------------------------------------------------------------


def chunk_text(raw_text: str, title: str = "") -> list[str]:
    """Split raw_text into overlapping windows, each prefixed with the title.

    The title rides on every chunk so an out-of-context middle window still
    carries what the document is. Read-only: raw_text is never mutated (plan.md's
    preservation guarantee) — this builds new strings.

    Returns an empty list for empty text, which makes indexing that document a
    no-op rather than an error.
    """
    text = (raw_text or "").strip()
    if not text:
        return []

    prefix = ""
    clean_title = (title or "").strip()
    if clean_title:
        prefix = f"{clean_title}\n\n"

    step = max(CHUNK_CHARS - CHUNK_OVERLAP, 1)
    windows: list[str] = []
    for start in range(0, len(text), step):
        window = text[start : start + CHUNK_CHARS].strip()
        if window:
            windows.append(prefix + window)
        if start + CHUNK_CHARS >= len(text) or len(windows) >= MAX_CHUNKS:
            break
    return windows


# --- Chroma store ---------------------------------------------------------


def _new_client():
    import chromadb

    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


def _new_collection(client):
    # cosine matches the normalized vectors above and plan.md §4's retrieval. No
    # embedding_function: we always pass our own vectors (embed_texts).
    return client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def _get_collection():
    """Return the cached collection, opening the store on first use."""
    global _client, _collection
    if _collection is not None:
        return _collection
    with _store_lock:
        if _collection is None:
            _client = _new_client()
            _collection = _new_collection(_client)
        return _collection


def _reset_collection() -> None:
    """Drop and recreate an empty collection. Caller is responsible for refilling.

    Deletes only the collection, not the whole directory — used by `reindex()`
    so a rebuild starts clean without removed documents lingering.
    """
    global _collection
    with _store_lock:
        client = _client or _new_client()
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:  # collection may not exist yet — that's fine
            pass
        _collection = _new_collection(client)


def _wipe_store() -> None:
    """Delete a corrupt store from disk so it can be rebuilt from scratch.

    Chroma caches its client in-process keyed by path, so removing the directory
    is not enough on its own — the cache is cleared too, or a "wiped" store would
    resurrect from memory the moment it is reopened at the same path.
    """
    global _client, _collection
    with _store_lock:
        _client = None
        _collection = None
        try:
            from chromadb.api.shared_system_client import SharedSystemClient

            SharedSystemClient.clear_system_cache()
        except Exception:  # never let a cache-clear quirk block recovery
            logger.debug("Could not clear Chroma system cache.", exc_info=True)
    shutil.rmtree(settings.chroma_dir, ignore_errors=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)


def indexed_count() -> int:
    """How many distinct documents the store holds. Opens it if it is not open.

    The health check's readiness probe: it fails loudly if the store is missing
    or corrupt, which is the failure `main.py` deliberately swallows at startup
    so a broken store cannot stop the app from booting.
    """
    return len(_indexed_doc_ids(_get_collection()))


def _indexed_doc_ids(collection) -> set[str]:
    """The set of distinct document ids currently present in the store."""
    got = collection.get(include=["metadatas"])
    return {
        m["doc_id"]
        for m in (got.get("metadatas") or [])
        if m and m.get("doc_id")
    }


# --- Writes ---------------------------------------------------------------


def add_document(*, doc_id: str, user_id: str, title: str, raw_text: str) -> int:
    """Chunk, embed, and upsert one document. Returns the chunk count written.

    Idempotent: existing chunks for `doc_id` are cleared first, so re-adding
    after a reindex or an edit replaces rather than duplicates. Returns 0 (a
    no-op) when there is no text to embed — the document stays unindexed rather
    than failing.
    """
    chunks = chunk_text(raw_text, title)
    delete_document(doc_id)  # replace, never duplicate
    if not chunks:
        return 0

    collection = _get_collection()
    ids = [f"{doc_id}:{i}" for i in range(len(chunks))]
    metadatas: list[dict[str, Any]] = [
        {"doc_id": doc_id, "user_id": user_id, "chunk_index": i}
        for i in range(len(chunks))
    ]
    collection.upsert(
        ids=ids,
        embeddings=embed_texts(chunks),
        metadatas=metadatas,
        documents=chunks,
    )
    return len(chunks)


def delete_document(doc_id: str) -> None:
    """Remove every chunk belonging to a document."""
    _get_collection().delete(where={"doc_id": doc_id})


# --- Query ----------------------------------------------------------------


def _collapse(
    metas: list[dict[str, Any]],
    dists: list[float],
    into: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fold one Chroma result row into {doc_id: best hit for that document}.

    Several chunks of one document can match; only the closest is kept. `into`
    lets a caller accumulate across several result rows — one per query vector —
    which is what the neighbour lookup below does.
    """
    best = into if into is not None else {}
    for meta, dist in zip(metas, dists):
        doc_id = meta.get("doc_id")
        if not doc_id:
            continue
        score = 1.0 - dist  # cosine distance -> similarity
        current = best.get(doc_id)
        if current is None or score > current["score"]:
            best[doc_id] = {
                "doc_id": doc_id,
                "score": score,
                "chunk_index": meta.get("chunk_index"),
            }
    return best


def _ranked(best: dict[str, dict[str, Any]], k: int) -> list[dict[str, Any]]:
    return sorted(best.values(), key=lambda r: r["score"], reverse=True)[:k]


def query(query_text: str, *, user_id: str, k: int = 5) -> list[dict[str, Any]]:
    """Semantic search. Returns up to k distinct documents, best-scoring first.

    Results are filtered to `user_id` — the isolation boundary. Even with a
    single user today, an unfiltered top-k would leak across users the moment
    auth lands (plan.md § Stretch Goals), so the filter is enforced here and
    mutation-tested. Each result is `{doc_id, score, chunk_index}`; the caller
    hydrates the full document from SQLite and links it to its original.

    `score` is cosine similarity in [0, 1]-ish (1.0 = identical). Several chunks
    of one document collapse to the single best-scoring chunk.
    """
    text = (query_text or "").strip()
    if not text:
        return []

    collection = _get_collection()
    pool = max(k * _QUERY_POOL_FACTOR, _QUERY_POOL_MIN)
    result = collection.query(
        query_embeddings=embed_texts([text]),
        n_results=pool,
        where={"user_id": user_id},
    )

    metas = (result.get("metadatas") or [[]])[0]
    dists = (result.get("distances") or [[]])[0]
    return _ranked(_collapse(metas, dists), k)


def neighbors_of_document(
    doc_id: str, *, user_id: str, k: int = 5
) -> list[dict[str, Any]]:
    """The k documents most similar to `doc_id`, using its **stored** vectors.

    **Why this exists rather than `query(document.raw_text)`.** The graph is
    computed on read (`graph/builder.py`), so embedding each document's text
    again on every graph load meant N model forward passes per page view — on an
    instance measured at ~1/40th this machine's CPU (see `ai/precomputed.py`).
    The vectors are already in the store from indexing, so a neighbour lookup
    needs **no inference at all**: fetch them back and query with them.

    It is also more faithful than the text path was. The model truncates at
    ~256 tokens, so embedding a whole `raw_text` compared documents by their
    *heads* only, while indexing had already chunked them — the graph and search
    disagreed about what a document was. Querying with the document's own chunks
    compares like with like.

    Returns the same `{doc_id, score, chunk_index}` shape as `query`, with the
    document itself removed. Empty when the document has no indexed chunks (no
    text, or not yet synced) — the caller draws no edges rather than failing.
    """
    collection = _get_collection()
    got = collection.get(where={"doc_id": doc_id}, include=["embeddings"])
    raw = got.get("embeddings")
    vectors = [] if raw is None else [list(v) for v in raw][:_NEIGHBOR_CHUNK_CAP]
    if not vectors:
        return []

    pool = max(k * _QUERY_POOL_FACTOR, _QUERY_POOL_MIN)
    result = collection.query(
        query_embeddings=vectors,
        n_results=pool,
        where={"user_id": user_id},
    )

    best: dict[str, dict[str, Any]] = {}
    for metas, dists in zip(
        result.get("metadatas") or [], result.get("distances") or []
    ):
        _collapse(metas, dists, into=best)
    best.pop(doc_id, None)  # a document is not its own neighbour
    return _ranked(best, k)


# --- Rebuild / sync -------------------------------------------------------


def reindex() -> int:
    """Rebuild the entire store from SQLite (the source of truth).

    Recovery path: a deleted or corrupt `data/chroma/` is regenerable because
    every document's raw_text lives in SQLite. Returns the number of documents
    indexed (those with embeddable text).
    """
    from db import database

    _reset_collection()
    count = 0
    for doc in database.documents_for_indexing():
        try:
            if add_document(
                doc_id=doc["id"],
                user_id=doc["user_id"],
                title=doc.get("title") or "",
                raw_text=doc.get("raw_text") or "",
            ):
                count += 1
        except Exception:
            logger.exception("Failed to index %s during reindex.", doc["id"])
    logger.info("Reindexed %d documents into Chroma.", count)
    return count


def ensure_synced() -> None:
    """Bring the store in line with SQLite. Safe to call on every startup.

    Two failure modes: a *corrupt* store (fails to open) is wiped and fully
    rebuilt; a merely *incomplete* one (documents added while it was gone, or a
    partial index) gets only the missing documents added. Both are cheap —
    embeddings are local and free — so recovery is silent and automatic.
    """
    from db import database

    try:
        indexed = _indexed_doc_ids(_get_collection())
    except Exception:
        logger.exception("Chroma store unreadable — wiping and rebuilding.")
        _wipe_store()
        reindex()
        return

    missing = [d for d in database.documents_for_indexing() if d["id"] not in indexed]
    if not missing:
        return

    logger.info("Indexing %d document(s) missing from Chroma.", len(missing))
    for doc in missing:
        try:
            add_document(
                doc_id=doc["id"],
                user_id=doc["user_id"],
                title=doc.get("title") or "",
                raw_text=doc.get("raw_text") or "",
            )
        except Exception:
            logger.exception("Failed to index %s during sync.", doc["id"])
