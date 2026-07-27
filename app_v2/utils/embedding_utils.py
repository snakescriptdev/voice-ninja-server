"""
Embedding generation for the FAISS-backed personal knowledge base, using a
free, locally-run Hugging Face sentence-transformers model via LangChain —
no external API calls or keys required.
"""

import threading

from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_embeddings = None
# Serializes the lazy load below — guards against a background preload (see
# main.py) and a real request racing to load the model at the same time.
_embeddings_lock = threading.Lock()


def get_embeddings():
    """Lazily loads and caches the LangChain HuggingFace embeddings wrapper on first use."""
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    with _embeddings_lock:
        if _embeddings is None:
            import torch
            # Pinned alongside faiss.omp_set_num_threads(1) in faiss_store.py —
            # torch and faiss each bring their own OpenMP runtime, which segfaults
            # when sharing a process unless both are limited to a single thread.
            # HuggingFaceEmbeddings loads a SentenceTransformer under the hood, so
            # this still applies even though we don't touch torch directly here.
            torch.set_num_threads(1)
            from langchain_huggingface import HuggingFaceEmbeddings
            logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
            _embeddings = HuggingFaceEmbeddings(
                model_name=EMBEDDING_MODEL_NAME,
                model_kwargs={"device": "cpu"},
                # Unit-normalized so FAISS inner-product search doubles as cosine
                # similarity search (see distance_strategy in faiss_store.py).
                encode_kwargs={"normalize_embeddings": True},
            )
    return _embeddings
