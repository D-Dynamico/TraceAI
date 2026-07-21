"""Local embedding generation + ChromaDB vector store (plan.md §4 Module 5).

The semantic half of search. Three things this module takes seriously:

1. **Embeddings are local and free.** `all-MiniLM-L6-v2` runs on CPU via
   sentence-transformers, so unlike the Gemini path in `categorizer.py` there is
   **no rate limiter** — do not add one. A batch upload embeds as fast as the CPU
   allows.

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
"""

from __future__ import annotations

import logging
import shutil
import threading
from typing import Any

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

_model = None
_model_lock = threading.Lock()
_client = None
_collection = None
_store_lock = threading.Lock()


# --- Embedding model ------------------------------------------------------


def _get_model():
    """Lazily load and cache the sentence-transformer.

    Deferred so importing this module (and starting the app) does not trigger the
    ~80MB model download; it happens on the first real embed. Tests stub
    `embed_texts`, so they never reach here.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model %s", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME)
        return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts as normalized vectors, cosine-ready.

    The single point every embedding flows through — add, query, and reindex all
    call it, which is why tests stub exactly this one function. Vectors are
    L2-normalized so cosine distance in Chroma matches plan.md §4's cosine
    similarity.
    """
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


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

    best: dict[str, dict[str, Any]] = {}
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

    ranked = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    return ranked[:k]


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
