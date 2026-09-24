"""Embedding provider contract.

Embeddings are a different job from text generation, and a different model:

    LLM (Groq)        text  -> text      summaries, answers
    embedding model   text  -> vector    "what does this passage mean?"

Groq does not produce embeddings, so they come from a separate provider behind
this interface. Nothing above this layer knows which one; nothing in here knows
Pinecone exists - so either side can change without touching the other.

    embed_documents(texts) -> List[List[float]]   indexing side
    embed_query(text)      -> List[float]         search side

The same provider and model embed both sides. That is a hard requirement: a
query vector is only comparable with passage vectors from the same model.
"""

from __future__ import annotations

import abc
from typing import List, Optional

from app.utils.errors import EmbeddingNotConfiguredError


class EmbeddingProvider(abc.ABC):
    """One embedding implementation, reduced to "turn text into vectors"."""

    #: Stable identifier, stored with the vectors for traceability.
    name: str = "provider"
    #: Human name used in logs and health reports.
    label: str = "Embedding provider"

    @property
    @abc.abstractmethod
    def model(self) -> str:
        """Configured embedding model id."""

    @property
    @abc.abstractmethod
    def dimensions(self) -> int:
        """Vector length this provider produces, which must match the index."""

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when the provider can run. A local check - never a network call."""

    @abc.abstractmethod
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed passages for storage. Returns one vector per input, in order."""

    @abc.abstractmethod
    async def embed_query(self, text: str) -> List[float]:
        """Embed a user's question for retrieval."""

    # ------------------------------------------------------------- helpers
    def not_configured_error(self, detail: str = "") -> EmbeddingNotConfiguredError:
        message = EmbeddingNotConfiguredError.message
        if detail:
            message = f"{message} ({detail})"
        return EmbeddingNotConfiguredError(message, details={"provider": self.name})

    async def check_connection(self) -> dict:
        """Operator-triggered check that the model loads and produces vectors."""
        report = {
            "provider": self.name,
            "label": self.label,
            "model": self.model,
            "configured": self.is_configured,
        }
        if not self.is_configured:
            return {
                **report,
                "reachable": False,
                "dimensions": self.dimensions,
                "message": "The embedding library is not installed or EMBEDDING_PROVIDER is "
                           "not supported. Run pip install -r requirements.txt.",
            }
        try:
            vector = await self.embed_query("connection check")
            return {
                **report,
                "reachable": True,
                "dimensions": len(vector),
                "message": f"{self.label} produced a {len(vector)}-dimension vector.",
            }
        except Exception as exc:  # noqa: BLE001 - health checks report, never raise
            return {
                **report,
                "reachable": False,
                "dimensions": self.dimensions,
                "message": getattr(exc, "message", str(exc)),
            }


def clean_for_embedding(text: Optional[str]) -> str:
    """Normalise text before embedding. Never mutates the stored record."""
    if not text:
        return ""
    return " ".join(str(text).split())
