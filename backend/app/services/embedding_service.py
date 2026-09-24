"""Embedding service - text in, vectors out.

Sits between the knowledge layer and the configured embedding provider, so
callers never import a provider directly:

    KnowledgeDocument[]  ->  EmbeddingService  ->  EmbeddingProvider  ->  vectors

It owns four things the provider should not:

* **provider selection** from ``EMBEDDING_PROVIDER`` via the registry;
* **refusing junk** - empty or meaningless passages are dropped before they are
  embedded, because an embedding of whitespace is worse than no vector;
* **pairing** each returned vector back to the document it came from, so the
  caller cannot mis-align them;
* the **model signature** (``model@dimensions``) that is stamped on every
  vector and every index fingerprint. It is what guarantees a query is only
  ever compared with passages embedded by the *same* model.

Nothing here knows that Pinecone exists, and nothing here calls Groq.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from app.config import get_logger, get_settings
from app.knowledge.documents import MIN_EMBEDDABLE_CHARS, KnowledgeDocument
from app.knowledge.embeddings import EMBEDDING_REGISTRY, EmbeddingProvider
from app.utils.errors import EmbeddingNotConfiguredError, EmbeddingRequestError, InvalidQueryError

logger = get_logger(__name__)


class EmbeddingService:
    def __init__(self, provider: Optional[EmbeddingProvider] = None) -> None:
        self._provider = provider or _build_default_provider()

    # ---------------------------------------------------------- properties
    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimensions(self) -> int:
        return self._provider.dimensions

    @property
    def signature(self) -> str:
        """Identity of the vector space, e.g. ``BAAI/bge-small-en-v1.5@384``.

        Two vectors are only comparable when their signatures match.
        """
        return f"{self.model}@{self.dimensions}"

    @property
    def is_configured(self) -> bool:
        """Local check - never a probe request."""
        return self._provider.is_configured

    # -------------------------------------------------------------- embed
    async def embed_documents(
        self, documents: Sequence[KnowledgeDocument]
    ) -> List[Tuple[KnowledgeDocument, List[float]]]:
        """Embed knowledge documents, returning (document, vector) pairs.

        Documents whose text is too short to carry meaning are skipped rather
        than embedded - they would only add noise to search results.
        """
        if not documents:
            return []

        usable = [
            document
            for document in documents
            if len((document.content or "").strip()) >= MIN_EMBEDDABLE_CHARS
        ]
        skipped = len(documents) - len(usable)
        if skipped:
            logger.info("Skipped %s document(s) with too little text to embed.", skipped)
        if not usable:
            return []

        vectors = await self._provider.embed_documents([d.content for d in usable])
        if len(vectors) != len(usable):
            raise EmbeddingRequestError(
                "The embedding model returned a different number of vectors "
                "than documents sent.",
                internal=f"documents={len(usable)} vectors={len(vectors)}",
            )
        return list(zip(usable, vectors))

    async def embed_query(self, query: str) -> List[float]:
        """Embed one search query - once per search, with the passages' model."""
        cleaned = (query or "").strip()
        if not cleaned:
            raise InvalidQueryError()
        return await self._provider.embed_query(cleaned)

    # ------------------------------------------------------------- health
    async def check_connection(self) -> dict:
        return await self._provider.check_connection()

    def preload(self) -> None:
        """Warm the model if the provider supports it (local models do)."""
        loader = getattr(self._provider, "preload", None)
        if callable(loader):
            loader()


class _UnavailableProvider(EmbeddingProvider):
    """Stand-in for an unknown ``EMBEDDING_PROVIDER``.

    Construction never raises, so a bad embedding setting cannot break meeting
    analysis or deletion (both touch the indexer). It reports itself as not
    configured, and any attempt to embed explains exactly what to fix.
    """

    name = "unavailable"
    label = "Embedding provider (unavailable)"

    def __init__(self, requested: str) -> None:
        self._requested = requested
        self._dimensions = get_settings().embedding_dimensions

    @property
    def model(self) -> str:
        return get_settings().embedding_model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_configured(self) -> bool:
        return False

    def _error(self) -> EmbeddingNotConfiguredError:
        return EmbeddingNotConfiguredError(
            f"EMBEDDING_PROVIDER '{self._requested}' is not supported. Supported: "
            f"{', '.join(sorted(EMBEDDING_REGISTRY))}. (Gemini embeddings were removed; "
            "set EMBEDDING_PROVIDER=fastembed.)",
            details={"provider": self._requested},
        )

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        raise self._error()

    async def embed_query(self, text: str) -> List[float]:
        raise self._error()


def _build_default_provider() -> EmbeddingProvider:
    settings = get_settings()
    requested = (settings.embedding_provider or "").strip().lower()
    provider_cls = EMBEDDING_REGISTRY.get(requested)
    if provider_cls is None:
        logger.warning(
            "EMBEDDING_PROVIDER '%s' is not supported; meeting search is disabled until "
            "it is set to one of: %s.", requested, ", ".join(sorted(EMBEDDING_REGISTRY)),
        )
        return _UnavailableProvider(requested)
    return provider_cls()
