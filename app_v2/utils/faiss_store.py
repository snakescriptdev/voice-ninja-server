"""
Per-user FAISS vector index for the personal knowledge base. Chunk text and
metadata live in Postgres (PersonalKnowledgeBaseChunkModel); the embeddings
themselves live here, in one FAISS index file per user on local disk, keyed by
each chunk's Postgres row id.
"""

import os
import threading
from typing import List
import numpy as np
import faiss
from app_v2.utils.embedding_utils import EMBEDDING_DIMENSION
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

FAISS_INDEX_DIR = "faiss_indexes"
if not os.path.exists(FAISS_INDEX_DIR):
    os.makedirs(FAISS_INDEX_DIR)

# Guards concurrent read-modify-write of a single user's index file — FAISS
# indexes aren't safe to load/mutate/save from multiple requests at once.
_lock = threading.Lock()


def _index_path(user_id: int) -> str:
    return os.path.join(FAISS_INDEX_DIR, f"user_{user_id}.index")


def load_index(user_id: int) -> "faiss.IndexIDMap":
    """Load a user's FAISS index from disk, or a fresh empty one if they don't have one yet."""
    path = _index_path(user_id)
    if os.path.exists(path):
        return faiss.read_index(path)
    # Inner product over unit-normalized vectors == cosine similarity.
    return faiss.IndexIDMap(faiss.IndexFlatIP(EMBEDDING_DIMENSION))


def add_embeddings(user_id: int, ids: List[int], embeddings: List[List[float]]) -> None:
    """Add (id, embedding) pairs to a user's FAISS index and persist it to disk."""
    if not ids:
        return
    with _lock:
        index = load_index(user_id)
        vectors = np.array(embeddings, dtype="float32")
        id_array = np.array(ids, dtype="int64")
        index.add_with_ids(vectors, id_array)
        faiss.write_index(index, _index_path(user_id))
    logger.info(f"Added {len(ids)} vectors to FAISS index for user {user_id}")


def remove_embeddings(user_id: int, ids: List[int]) -> None:
    """Remove vectors by id from a user's FAISS index and persist it to disk. Best-effort."""
    if not ids:
        return
    path = _index_path(user_id)
    if not os.path.exists(path):
        return
    with _lock:
        index = faiss.read_index(path)
        index.remove_ids(np.array(ids, dtype="int64"))
        faiss.write_index(index, path)
    logger.info(f"Removed {len(ids)} vectors from FAISS index for user {user_id}")


def search_index(user_id: int, embedding: List[float], top_k: int = 5):
    """Search a user's FAISS index. Returns (distances, ids) arrays, or (None, None) if empty."""
    index = load_index(user_id)
    if index.ntotal == 0:
        return None, None
    distances, ids = index.search(np.array([embedding], dtype="float32"), min(top_k, index.ntotal))
    return distances[0], ids[0]
