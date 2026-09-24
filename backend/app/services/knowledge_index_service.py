"""Knowledge indexing - Supabase records become searchable vectors.

    meeting -> KnowledgeRepository -> build_documents -> EmbeddingService
            -> VectorRepository (Pinecone upsert) -> index status recorded

Three rules shape this file:

1. **Fingerprint first.** Before embedding anything, the documents - and the
   embedding model's identity - are hashed. If the hash matches what was
   stored at the last successful index, nothing has changed, so nothing is
   re-embedded. Changing the embedding model changes every hash, so every
   meeting is re-embedded with the new model rather than keeping old vectors.
2. **Deterministic ids.** Vector ids are ``{meeting_id}#{type}#{source}#{chunk}``,
   so re-indexing *updates* vectors rather than adding duplicates.
3. **Stale vectors are removed.** If a re-index produces fewer documents than
   last time (a decision was deleted, a transcript re-generated), the vectors
   that no longer have a source are deleted, so search can never return a row
   that no longer exists.

Indexing never fails a meeting. A meeting whose vectors could not be written is
still a complete, valid meeting - it is simply marked ``FAILED`` for indexing
and can be retried, which is the behaviour a production system needs when a
third-party index is briefly unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import get_logger, get_settings
from app.knowledge.documents import KnowledgeDocument, build_documents, fingerprint
from app.models.enums import IndexStatus
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.vector_repository import VectorRecord, VectorRepository
from app.services.embedding_service import EmbeddingService
from app.utils.errors import (
    AppError,
    EmbeddingNotConfiguredError,
    VectorStoreNotConfiguredError,
)
from app.utils.text import truncate

logger = get_logger(__name__)


@dataclass
class IndexResult:
    """What happened to one meeting. Never raises; always reports."""

    meeting_id: str
    status: IndexStatus
    documents: int = 0
    vectors_written: int = 0
    vectors_removed: int = 0
    skipped_unchanged: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status is IndexStatus.INDEXED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meeting_id": self.meeting_id,
            "status": self.status.value,
            "documents": self.documents,
            "vectors_written": self.vectors_written,
            "vectors_removed": self.vectors_removed,
            "skipped_unchanged": self.skipped_unchanged,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class BulkIndexResult:
    """Outcome of a backfill over many historical meetings."""

    processed: int = 0
    indexed: int = 0
    skipped_unchanged: int = 0
    failed: int = 0
    vectors_written: int = 0
    results: List[IndexResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "processed": self.processed,
            "indexed": self.indexed,
            "skipped_unchanged": self.skipped_unchanged,
            "failed": self.failed,
            "vectors_written": self.vectors_written,
            "meetings": [result.to_dict() for result in self.results],
        }


class KnowledgeIndexService:
    def __init__(
        self,
        knowledge: Optional[KnowledgeRepository] = None,
        embeddings: Optional[EmbeddingService] = None,
        vectors: Optional[VectorRepository] = None,
    ) -> None:
        self._knowledge = knowledge or KnowledgeRepository()
        self._embeddings = embeddings or EmbeddingService()
        self._vectors = vectors or VectorRepository()

    # ---------------------------------------------------------- properties
    @property
    def is_configured(self) -> bool:
        return self._embeddings.is_configured and self._vectors.is_configured

    def require_configured(self) -> None:
        """Fail early, with a message naming the variable that is missing."""
        if not self._embeddings.is_configured:
            raise EmbeddingNotConfiguredError()
        if not self._vectors.is_configured:
            raise VectorStoreNotConfiguredError()

    # ------------------------------------------------------- index one
    async def index_meeting(self, meeting_id: str, *, force: bool = False) -> IndexResult:
        """Make one meeting searchable.

        ``force`` re-embeds even when the content fingerprint is unchanged; the
        default reuses the existing vectors and spends nothing.
        """
        self.require_configured()

        bundle = self._knowledge.get_meeting_bundle(meeting_id)
        documents = build_documents(bundle)

        if not documents:
            logger.info("Meeting %s has nothing to index yet.", meeting_id)
            self._knowledge.mark_index_status(meeting_id, IndexStatus.NOT_INDEXED)
            return IndexResult(meeting_id=meeting_id, status=IndexStatus.NOT_INDEXED)

        content_hash = fingerprint(documents, self._embeddings.signature)
        if not force and self._is_unchanged(meeting_id, content_hash):
            logger.info(
                "Meeting %s is already indexed and unchanged; no embeddings requested.",
                meeting_id,
            )
            return IndexResult(
                meeting_id=meeting_id,
                status=IndexStatus.INDEXED,
                documents=len(documents),
                skipped_unchanged=True,
            )

        self._knowledge.mark_index_status(meeting_id, IndexStatus.INDEXING)

        try:
            written, removed = await self._write(meeting_id, documents)
        except AppError as exc:
            logger.error(
                "Indexing failed for meeting %s: %s (%s)", meeting_id, exc.code, exc.message
            )
            self._knowledge.mark_index_status(
                meeting_id, IndexStatus.FAILED, error=truncate(exc.message, 300)
            )
            return IndexResult(
                meeting_id=meeting_id,
                status=IndexStatus.FAILED,
                documents=len(documents),
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception as exc:  # noqa: BLE001 - indexing must never crash a request
            logger.exception("Unexpected indexing failure for meeting %s", meeting_id)
            self._knowledge.mark_index_status(
                meeting_id, IndexStatus.FAILED, error=truncate(str(exc), 300)
            )
            return IndexResult(
                meeting_id=meeting_id,
                status=IndexStatus.FAILED,
                documents=len(documents),
                error_code="INDEXING_FAILED",
                error_message="The meeting could not be indexed. Try again.",
            )

        self._knowledge.mark_index_status(
            meeting_id, IndexStatus.INDEXED, fingerprint=content_hash
        )
        logger.info(
            "Indexed meeting %s: %s document(s), %s vector(s) written, %s removed",
            meeting_id, len(documents), written, removed,
        )
        return IndexResult(
            meeting_id=meeting_id,
            status=IndexStatus.INDEXED,
            documents=len(documents),
            vectors_written=written,
            vectors_removed=removed,
        )

    async def index_meeting_safely(self, meeting_id: str, *, force: bool = False) -> IndexResult:
        """``index_meeting`` that cannot raise.

        Used by the automatic hook after analysis: a Pinecone outage must not
        turn a successfully analysed meeting into a failed request.
        """
        try:
            return await self.index_meeting(meeting_id, force=force)
        except AppError as exc:
            logger.warning(
                "Automatic indexing skipped for meeting %s: %s", meeting_id, exc.code
            )
            return IndexResult(
                meeting_id=meeting_id,
                status=IndexStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Automatic indexing crashed for meeting %s", meeting_id)
            return IndexResult(
                meeting_id=meeting_id,
                status=IndexStatus.FAILED,
                error_code="INDEXING_FAILED",
                error_message="The meeting could not be indexed.",
            )

    # -------------------------------------------------- index many (backfill)
    async def index_all(
        self, *, limit: Optional[int] = None, force: bool = False
    ) -> BulkIndexResult:
        """Backfill historical meetings, one at a time.

        Sequential on purpose: parallel indexing would multiply the embedding
        request rate and trip a free-tier limit, which is exactly what this
        project has to avoid.
        """
        self.require_configured()

        settings = get_settings()
        ceiling = limit or settings.knowledge_max_backfill_meetings
        candidates = self._knowledge.list_indexable_meetings(limit=ceiling)
        logger.info("Backfill starting: %s candidate meeting(s)", len(candidates))

        summary = BulkIndexResult()
        for row in candidates:
            meeting_id = str(row.get("id") or "")
            if not meeting_id:
                continue
            result = await self.index_meeting_safely(meeting_id, force=force)
            summary.processed += 1
            summary.vectors_written += result.vectors_written
            summary.results.append(result)
            if result.skipped_unchanged:
                summary.skipped_unchanged += 1
            elif result.succeeded:
                summary.indexed += 1
            elif result.status is IndexStatus.FAILED:
                summary.failed += 1

        logger.info(
            "Backfill finished: %s processed, %s indexed, %s unchanged, %s failed",
            summary.processed, summary.indexed, summary.skipped_unchanged, summary.failed,
        )
        return summary

    # ------------------------------------------------------------- delete
    async def delete_meeting(self, meeting_id: str) -> int:
        """Remove one meeting's vectors so search cannot return stale results."""
        if not self._vectors.is_configured:
            return 0
        removed = await self._vectors.delete_by_meeting(meeting_id)
        self._knowledge.mark_index_status(meeting_id, IndexStatus.NOT_INDEXED, fingerprint=None)
        return removed

    async def delete_meeting_safely(self, meeting_id: str) -> int:
        """``delete_meeting`` that cannot raise, for the meeting-delete hook.

        A meeting must still be deletable when Pinecone is unreachable; the
        orphaned vectors are cleaned up by the next re-index or delete.
        """
        try:
            return await self.delete_meeting(meeting_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not remove vectors for deleted meeting %s; they will be "
                "cleaned up on the next index run.", meeting_id,
            )
            return 0

    # -------------------------------------------------------------- status
    def status(self) -> Dict[str, Any]:
        """Configuration + coverage. Makes no external calls."""
        counts = self._knowledge.index_summary(signature=self._embeddings.signature)
        return {
            "embeddings_configured": self._embeddings.is_configured,
            "vector_store_configured": self._vectors.is_configured,
            "ready": self.is_configured,
            "embedding_provider": self._embeddings.provider_name,
            "embedding_model": self._embeddings.model,
            "embedding_dimensions": self._embeddings.dimensions,
            "embedding_signature": self._embeddings.signature,
            "index_name": self._vectors.index_name,
            "namespace": self._vectors.namespace,
            "tracks_index_status": self._knowledge.tracks_index_status,
            "counts": counts,
        }

    # ----------------------------------------------------------- internals
    def _is_unchanged(self, meeting_id: str, content_hash: str) -> bool:
        """True when this exact content was already indexed successfully."""
        state = self._knowledge.get_index_state(meeting_id)
        if not state:
            return False
        return (
            state.get("index_status") == IndexStatus.INDEXED.value
            and state.get("knowledge_fingerprint") == content_hash
        )

    async def _write(
        self, meeting_id: str, documents: List[KnowledgeDocument]
    ) -> tuple[int, int]:
        """Embed, upsert, then drop vectors whose source no longer exists."""
        # Which vectors exist today? Needed to spot the stale ones afterwards.
        try:
            existing_ids = set(await self._vectors.list_ids_for_meeting(meeting_id))
        except AppError:
            # Listing is a nicety; failing it must not block the upsert.
            logger.warning(
                "Could not list existing vectors for meeting %s; skipping stale cleanup.",
                meeting_id,
            )
            existing_ids = set()

        pairs = await self._embeddings.embed_documents(documents)
        records = [
            VectorRecord(
                id=document.vector_id,
                values=vector,
                # The model is stamped on every vector so search can refuse to
                # compare a query with vectors from a different model.
                metadata={**document.to_metadata(), "embedding_model": self._embeddings.model},
            )
            for document, vector in pairs
        ]

        written = await self._vectors.upsert(records)

        stale = existing_ids - {record.id for record in records}
        removed = await self._vectors.delete_by_ids(sorted(stale)) if stale else 0
        if removed:
            logger.info(
                "Removed %s stale vector(s) for meeting %s whose source records are gone.",
                removed, meeting_id,
            )
        return written, removed
