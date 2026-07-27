"""
Per-user FAISS vector store for the personal knowledge base, built on
LangChain's FAISS wrapper around a local Hugging Face embedding model. Chunk
text and metadata live in Postgres (PersonalKnowledgeBaseChunkModel); the
embeddings themselves live here, in one FAISS store per user on local disk,
keyed by each chunk's Postgres row id (carried as `metadata["chunk_id"]` and
as the docstore id, so results and deletes map straight back to Postgres).
Each vector's owning KB item is also carried as `metadata["kb_id"]`, so a
search can be scoped to a specific set of KB items (e.g. the ones attached
to a given agent) directly at FAISS search time, not via a separate
Postgres join afterward.
"""

import os
import threading
from typing import List, Optional, Tuple

import faiss
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy

from app_v2.utils.embedding_utils import get_embeddings
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

# faiss and torch each bring their own OpenMP runtime; sharing a process
# (as this app does — embedding via torch, indexing via faiss) segfaults on
# interpreter/process exit unless faiss is pinned to a single thread. Our
# per-request index sizes are tiny, so this costs nothing in practice.
faiss.omp_set_num_threads(1)

FAISS_INDEX_DIR = "faiss_indexes"
if not os.path.exists(FAISS_INDEX_DIR):
    os.makedirs(FAISS_INDEX_DIR)

# Guards concurrent read-modify-write of a single user's store — FAISS
# stores aren't safe to load/mutate/save from multiple requests at once.
_lock = threading.Lock()


def _index_name(user_id: int) -> str:
    return f"user_{user_id}"


def _store_exists(user_id: int) -> bool:
    return os.path.exists(os.path.join(FAISS_INDEX_DIR, f"{_index_name(user_id)}.faiss"))


def load_store(user_id: int) -> Optional[FAISS]:
    """Load a user's FAISS store from disk, or None if they don't have one yet."""
    if not _store_exists(user_id):
        return None
    try:
        return FAISS.load_local(
            FAISS_INDEX_DIR,
            get_embeddings(),
            index_name=_index_name(user_id),
            allow_dangerous_deserialization=True,
            distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT,
        )
    except Exception as e:
        logger.warning(f"Failed to load FAISS store for user {user_id}, treating as empty: {e}")
        return None


def add_embeddings(user_id: int, ids: List[int], texts: List[str], kb_ids: List[int]) -> None:
    """Embed `texts` and add them to a user's FAISS store under `ids`, then persist to disk.
    `kb_ids[i]` is the owning KB item id for `ids[i]`/`texts[i]` — stored in
    each vector's metadata so search can later be scoped to specific KB items."""
    if not ids:
        return
    documents = [
        Document(page_content=text, metadata={"chunk_id": chunk_id, "kb_id": kb_id})
        for chunk_id, text, kb_id in zip(ids, texts, kb_ids)
    ]
    str_ids = [str(chunk_id) for chunk_id in ids]
    with _lock:
        store = load_store(user_id)
        if store is None:
            store = FAISS.from_documents(
                documents, get_embeddings(), ids=str_ids, distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT
            )
        else:
            store.add_documents(documents, ids=str_ids)
        store.save_local(FAISS_INDEX_DIR, index_name=_index_name(user_id))
    logger.info(f"Added {len(ids)} vectors to FAISS store for user {user_id}")


def remove_embeddings(user_id: int, ids: List[int]) -> None:
    """Remove vectors by id from a user's FAISS store and persist it to disk. Best-effort."""
    if not ids:
        return
    with _lock:
        store = load_store(user_id)
        if store is None:
            return
        str_ids = [str(chunk_id) for chunk_id in ids]
        # store.delete() raises if asked to delete an id it doesn't have —
        # filter down first so already-gone/stale ids are silently skipped.
        present_ids = [i for i in str_ids if i in store.index_to_docstore_id.values()]
        if not present_ids:
            return
        store.delete(ids=present_ids)
        store.save_local(FAISS_INDEX_DIR, index_name=_index_name(user_id))
    logger.info(f"Removed {len(present_ids)} vectors from FAISS store for user {user_id}")


def search_index(user_id: int, query: str, top_k: int = 5, kb_ids: Optional[List[int]] = None) -> List[Tuple[int, float]]:
    """
    Search a user's FAISS store by raw query text — embedding happens
    internally. If `kb_ids` is given, only vectors whose `metadata["kb_id"]`
    is in that list are considered (filtered inside FAISS itself, before
    ranking/truncating to `top_k` — not a post-hoc approximation). Returns
    (chunk_id, score) tuples, best match first; score is cosine similarity
    (higher is better). Empty list if the user has no store yet, or if
    `kb_ids` is an empty list (nothing to search).
    """
    if kb_ids is not None and not kb_ids:
        return []
    store = load_store(user_id)
    if store is None:
        return []
    search_kwargs = {}
    if kb_ids is not None:
        search_kwargs["filter"] = {"kb_id": kb_ids}
        # FAISS filters by pulling `fetch_k` candidates and filtering down to
        # `k` — must be generous enough that the true top-k within `kb_ids`
        # isn't lost to unrelated vectors crowding out the initial fetch.
        search_kwargs["fetch_k"] = max(top_k * 10, 50)
    results = store.similarity_search_with_score(query, k=top_k, **search_kwargs)
    return [(doc.metadata["chunk_id"], float(score)) for doc, score in results]
