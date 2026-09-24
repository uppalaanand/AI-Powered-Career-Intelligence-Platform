"""Embedding providers.

    EmbeddingProvider (contract)
        `- FastEmbedProvider   local ONNX model, no API key (default)

Kept separate from ``app.repositories.vector_repository`` on purpose: embedding
generation and vector storage are independent concerns, so either can be
swapped without touching the other. Groq is not here - it generates text, not
embeddings.
"""

from app.knowledge.embeddings.base import EmbeddingProvider, clean_for_embedding
from app.knowledge.embeddings.fastembed_provider import FastEmbedProvider

#: Name -> class. ``EMBEDDING_PROVIDER`` is resolved through this registry.
EMBEDDING_REGISTRY = {FastEmbedProvider.name: FastEmbedProvider}

__all__ = [
    "EMBEDDING_REGISTRY",
    "EmbeddingProvider",
    "FastEmbedProvider",
    "clean_for_embedding",
]
