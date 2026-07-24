"""
Embedding generation for the pgvector-backed personal knowledge base, using a
free, locally-run Hugging Face sentence-transformers model — no external API
calls or keys required.
"""

from typing import List
from app_v2.core.logger import setup_logger

logger = setup_logger(__name__)

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384

_model = None


def _get_model():
    """Lazily loads and caches the sentence-transformers model on first use."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}")
        _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _model


def generate_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Generate a unit-normalized embedding vector for each input text, in the
    same order. Normalized so FAISS inner-product search doubles as cosine
    similarity search.
    """
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False, normalize_embeddings=True)
    return [vector.tolist() for vector in vectors]


def generate_embedding(text: str) -> List[float]:
    """Generate an embedding vector for a single piece of text."""
    return generate_embeddings([text])[0]
