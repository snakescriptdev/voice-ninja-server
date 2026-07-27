"""Token-based text chunking for the pgvector-backed personal knowledge base."""

from typing import List
import tiktoken

_encoding = tiktoken.get_encoding("cl100k_base")

DEFAULT_CHUNK_SIZE = 300
DEFAULT_CHUNK_OVERLAP = 50


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[str]:
    """
    Split text into overlapping chunks of at most `chunk_size` tokens, so each
    chunk stays within the embedding model's context and retains some context
    from the previous chunk via `overlap` tokens.
    """
    text = text.strip()
    if not text:
        return []

    tokens = _encoding.encode(text)
    if len(tokens) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = chunk_size - overlap
    while start < len(tokens):
        chunk_tokens = tokens[start : start + chunk_size]
        chunks.append(_encoding.decode(chunk_tokens).strip())
        start += step

    return [c for c in chunks if c]
