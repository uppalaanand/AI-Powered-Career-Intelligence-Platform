"""Semantic search over historical meetings (Milestone 3, Task 4).

    query -> 1 embedding request -> 1 vector search -> 1 database read -> results

Three network calls, fixed, regardless of how many meetings exist. That is what
keeps the search inside the three-second budget, and it is why this service
**never calls an LLM**: finding which meeting discussed something is a vector
problem, not a generation problem. The LLM is only involved when the user asks
for an *answer* (see ``rag_service``).

Why this is not keyword search
------------------------------
The query is embedded into the same vector space as the meeting passages, so
"which meeting discussed the database migration?" matches a passage that says
"we need to move the Postgres schema over" without sharing a single keyword.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.config import get_logger, get_settings
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.vector_repository import VectorMatch, VectorRepository
from app.schemas.search import (
    SearchFilters,
    SearchMatch,
    SearchResponse,
    SearchResult,
)
from app.services.embedding_service import EmbeddingService
from app.utils.errors import (
    AppError,
    EmbeddingNotConfiguredError,
    InvalidQueryError,
    SearchUnavailableError,
    VectorStoreNotConfiguredError,
)
from app.utils.text import truncate

logger = get_logger(__name__)

NO_RESULTS_MESSAGE = "No relevant meetings were found for this query."
NOT_INDEXED_MESSAGE = (
    "No meetings have been indexed yet. Index your meetings to make them searchable."
)


@dataclass
class RetrievedPassage:
    """A vector match after the meeting record behind it has been resolved."""

    meeting_id: str
    meeting_title: str
    meeting_date: Optional[str]
    source_type: str
    source_id: str
    chunk_index: int
    content: str
    score: float
    status: Optional[str] = None


class SemanticSearchService:
    def __init__(
        self,
        embeddings: Optional[EmbeddingService] = None,
        vectors: Optional[VectorRepository] = None,
        knowledge: Optional[KnowledgeRepository] = None,
    ) -> None:
        self._embeddings = embeddings or EmbeddingService()
        self._vectors = vectors or VectorRepository()
        self._knowledge = knowledge or KnowledgeRepository()

    @property
    def is_configured(self) -> bool:
        return self._embeddings.is_configured and self._vectors.is_configured

    # -------------------------------------------------------------- search
    async def search(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        filters: Optional[SearchFilters] = None,
    ) -> SearchResponse:
        """Public search: returns meetings, grouped and ranked."""
        started = time.perf_counter()
        passages = await self.retrieve(query, top_k=top_k, filters=filters)
        results = _group_by_meeting(passages)
        took_ms = int((time.perf_counter() - started) * 1000)

        logger.info(
            "Semantic search finished in %sms: %s passage(s) across %s meeting(s)",
            took_ms, len(passages), len(results),
        )
        return SearchResponse(
            query=query,
            results=results,
            total_matches=len(passages),
            took_ms=took_ms,
            message=None if results else NO_RESULTS_MESSAGE,
        )

    async def retrieve(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        filters: Optional[SearchFilters] = None,
    ) -> List[RetrievedPassage]:
        """Embed, search, resolve. Shared by search and RAG so a question is
        embedded **once** no matter which endpoint asked for it."""
        cleaned = (query or "").strip()
        if not cleaned:
            raise InvalidQueryError()

        self._require_configured()
        settings = get_settings()
        limit = min(top_k or settings.search_default_top_k, settings.search_max_top_k)

        try:
            vector = await self._embeddings.embed_query(cleaned)
            matches = await self._vectors.query(
                vector,
                top_k=limit,
                metadata_filter=scope_to_model(
                    build_metadata_filter(filters), self._embeddings.model
                ),
            )
        except (EmbeddingNotConfiguredError, VectorStoreNotConfiguredError):
            raise
        except AppError as exc:
            # One user-facing message for any retrieval failure; the specific
            # cause is already in the logs and must not leak to the browser.
            logger.error("Semantic search failed: %s - %s", exc.code, exc.message)
            raise SearchUnavailableError(details={"reason": exc.code}) from exc

        threshold = settings.search_min_score
        usable = [match for match in matches if match.score >= threshold]
        if not usable:
            return []

        return self._resolve(usable)

    # ----------------------------------------------------------- internals
    def _require_configured(self) -> None:
        if not self._embeddings.is_configured:
            raise EmbeddingNotConfiguredError()
        if not self._vectors.is_configured:
            raise VectorStoreNotConfiguredError()

    def _resolve(self, matches: Sequence[VectorMatch]) -> List[RetrievedPassage]:
        """Attach live meeting records to the matches.

        One batched database read for every meeting in the result set, not one
        per match. Matches whose meeting has since been deleted are dropped -
        a stale vector must never surface a meeting that no longer exists.
        """
        meeting_ids = [match.meeting_id for match in matches if match.meeting_id]
        headers = self._knowledge.get_meeting_headers(meeting_ids)

        passages: List[RetrievedPassage] = []
        dropped = 0
        for match in matches:
            header = headers.get(match.meeting_id)
            if not header:
                dropped += 1
                continue
            passages.append(
                RetrievedPassage(
                    meeting_id=match.meeting_id,
                    meeting_title=str(header.get("title") or "Untitled meeting"),
                    meeting_date=_as_text(header.get("created_at")),
                    source_type=match.source_type or "transcript",
                    source_id=str(match.metadata.get("source_id") or ""),
                    chunk_index=int(match.metadata.get("chunk_index") or 0),
                    content=match.content,
                    score=round(float(match.score), 4),
                    status=header.get("status"),
                )
            )

        if dropped:
            logger.warning(
                "Dropped %s search match(es) whose meeting no longer exists. "
                "Re-index to clear the stale vectors.", dropped,
            )
        return passages


# ------------------------------------------------------------------ filters
def build_metadata_filter(filters: Optional[SearchFilters]) -> Optional[Dict[str, Any]]:
    """Translate API filters into a Pinecone metadata filter.

    Applied *inside* the vector query so the index does the work and the
    requested number of results actually comes back.
    """
    if filters is None or filters.is_empty:
        return None

    clauses: List[Dict[str, Any]] = []
    if filters.meeting_id:
        clauses.append({"meeting_id": {"$eq": filters.meeting_id}})
    if filters.source_type:
        clauses.append({"source_type": {"$eq": filters.source_type}})
    if filters.participant:
        clauses.append({"assigned_to": {"$eq": filters.participant}})

    date_range: Dict[str, Any] = {}
    start = _timestamp(filters.date_from)
    end = _timestamp(filters.date_to, end_of_day=True)
    if start is not None:
        date_range["$gte"] = start
    if end is not None:
        date_range["$lte"] = end
    if date_range:
        clauses.append({"meeting_date_ts": date_range})

    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def scope_to_model(
    metadata_filter: Optional[Dict[str, Any]], model: str
) -> Dict[str, Any]:
    """Restrict a query to vectors produced by the current embedding model.

    Similarity between vectors from two different models is meaningless, so a
    query must never be compared against another model's vectors - even if one
    was left in the index from before a model change.
    """
    clause = {"embedding_model": {"$eq": model}}
    if not metadata_filter:
        return clause
    if "$and" in metadata_filter:
        return {"$and": [*metadata_filter["$and"], clause]}
    return {"$and": [metadata_filter, clause]}


def _timestamp(value: Optional[str], *, end_of_day: bool = False) -> Optional[float]:
    """ISO date/datetime -> POSIX timestamp, matching `meeting_date_ts`."""
    if not value:
        return None
    from datetime import datetime, time as dt_time

    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(f"{text}T00:00:00")
        except ValueError:
            logger.warning("Ignoring unparseable date filter: %s", truncate(str(value), 40))
            return None

    if end_of_day and parsed.time() == dt_time(0, 0):
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed.timestamp()


# ------------------------------------------------------------------ grouping
def _group_by_meeting(passages: Sequence[RetrievedPassage]) -> List[SearchResult]:
    """Collapse passages into one result per meeting, best score first.

    "Which meeting discussed X" should answer with meetings, not with a list of
    text fragments that happen to repeat the same meeting five times.
    """
    grouped: Dict[str, SearchResult] = {}

    for passage in passages:
        result = grouped.get(passage.meeting_id)
        if result is None:
            grouped[passage.meeting_id] = SearchResult(
                meeting_id=passage.meeting_id,
                meeting_title=passage.meeting_title,
                meeting_date=passage.meeting_date,
                status=passage.status,
                score=passage.score,
                matched_source_types=[passage.source_type],
                excerpt=passage.content,
                matches=[_to_match(passage)],
            )
            continue

        result.matches.append(_to_match(passage))
        if passage.source_type not in result.matched_source_types:
            result.matched_source_types.append(passage.source_type)
        if passage.score > result.score:
            # The excerpt shown is always the strongest passage for that meeting.
            result.score = passage.score
            result.excerpt = passage.content

    return sorted(grouped.values(), key=lambda result: result.score, reverse=True)


def _to_match(passage: RetrievedPassage) -> SearchMatch:
    return SearchMatch(
        meeting_id=passage.meeting_id,
        source_type=passage.source_type,
        source_id=passage.source_id or None,
        chunk_index=passage.chunk_index,
        content=passage.content,
        score=passage.score,
    )


def _as_text(value: Any) -> Optional[str]:
    return str(value) if value else None
