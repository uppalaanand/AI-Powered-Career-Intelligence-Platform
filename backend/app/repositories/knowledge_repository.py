"""Reads a meeting's complete knowledge bundle out of Supabase.

This is a *composer*, not a new data layer. Everything it returns already has a
home:

    MeetingRepository        -> the meeting row (title, date, transcript text)
    IntelligenceRepository   -> summary, key points, decisions, action items,
                                participants

Re-querying those tables here would be a second implementation of code that
already works, so this class calls the existing repositories and adds only what
Milestone 3 genuinely needs: the index bookkeeping columns.

Index status is stored on ``meetings`` (three nullable columns added by
migration 002) rather than in a new table - the lightest thing that tracks
NOT_INDEXED / INDEXING / INDEXED / FAILED. If the migration has not been run,
every write here degrades to a logged warning and indexing still works; only
the bookkeeping is lost. That mirrors how ``meeting_summaries.provider`` was
handled in Milestone 2.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import get_logger
from app.models.enums import IndexStatus
from app.repositories import supabase_client as db
from app.repositories.intelligence_repository import IntelligenceRepository
from app.repositories.meeting_repository import MeetingRepository
from app.utils.errors import AppError
from app.utils.timefmt import utc_now_iso

logger = get_logger(__name__)

MEETINGS_TABLE = "meetings"

#: Columns added by database/migrations/002_add_knowledge_index_columns.sql.
INDEX_COLUMNS = ("index_status", "indexed_at", "index_error", "knowledge_fingerprint")

#: Flipped off permanently the first time Supabase reports the columns missing,
#: so a pre-migration install is not re-probed on every meeting.
_index_columns_available = True


class KnowledgeRepository:
    """Meeting knowledge reads + index bookkeeping."""

    def __init__(
        self,
        meetings: Optional[MeetingRepository] = None,
        intelligence: Optional[IntelligenceRepository] = None,
    ) -> None:
        self._meetings = meetings or MeetingRepository()
        self._intelligence = intelligence or IntelligenceRepository()

    # ----------------------------------------------------------------- read
    def get_meeting_bundle(self, meeting_id: str) -> Dict[str, Any]:
        """Everything about one meeting that is worth making searchable.

        Shape (all keys always present, values may be empty)::

            {"meeting": {...}, "summary": str, "key_points": [...],
             "decisions": [...], "action_items": [...], "participants": [...]}
        """
        meeting = self._meetings.get_or_404(meeting_id)
        stored = self._intelligence.get_intelligence(meeting_id) or {}

        return {
            "meeting": meeting,
            "summary": stored.get("summary") or "",
            "key_points": stored.get("key_points") or [],
            "decisions": stored.get("decisions") or [],
            "action_items": stored.get("action_items") or [],
            "participants": stored.get("participants") or [],
            "has_intelligence": bool(stored),
        }

    def get_meeting_headers(self, meeting_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Title/date/status for many meetings in **one** query.

        Search resolves several meetings at once; looping ``get()`` per id would
        turn one round-trip into N and blow the three-second budget.
        """
        unique = [mid for mid in dict.fromkeys(meeting_ids) if mid]
        if not unique:
            return {}

        response = db.run(
            lambda: db.table(MEETINGS_TABLE)
            .select("id,title,status,media_kind,duration_seconds,created_at")
            .in_("id", unique)
            .execute(),
            action="meetings.headers",
        )
        return {str(row.get("id")): row for row in (response.data or [])}

    def list_indexable_meetings(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        """Historical meetings worth indexing: anything with a transcript.

        Analysis is not required - a transcribed meeting is already searchable.
        Ordered oldest-first so a backfill makes steady, resumable progress.
        """
        def query() -> Any:
            columns = "id,title,status,created_at,transcript_word_count"
            if _index_columns_available:
                columns += ",index_status,knowledge_fingerprint,indexed_at"
            return (
                db.table(MEETINGS_TABLE)
                .select(columns)
                .not_.is_("transcript_text", "null")
                .order("created_at", desc=False)
                .limit(max(1, limit))
                .execute()
            )

        try:
            response = db.run(query, action="meetings.indexable")
        except AppError as exc:
            if _index_columns_available and _is_missing_index_column(exc):
                _disable_index_columns()
                return self.list_indexable_meetings(limit=limit)
            raise
        return response.data or []

    def index_summary(self, *, signature: Optional[str] = None) -> Dict[str, int]:
        """Coverage counts for the status endpoint. One query, not N.

        With ``signature`` (the current embedding model), a meeting indexed by a
        *different* model is counted as ``STALE`` rather than ``INDEXED``: its
        vectors exist but cannot be compared with today's query vectors, so it
        needs re-indexing before it is searchable again.
        """
        if not _index_columns_available:
            return {}
        try:
            response = db.run(
                lambda: db.table(MEETINGS_TABLE)
                .select("index_status,knowledge_fingerprint")
                .execute(),
                action="meetings.index_summary",
            )
        except AppError as exc:
            if _is_missing_index_column(exc):
                _disable_index_columns()
                return {}
            raise

        counts: Dict[str, int] = {}
        for row in response.data or []:
            key = row.get("index_status") or IndexStatus.NOT_INDEXED.value
            stored = row.get("knowledge_fingerprint") or ""
            if (
                signature
                and key == IndexStatus.INDEXED.value
                and not stored.startswith(f"{signature}:")
            ):
                key = "STALE"
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ---------------------------------------------------------- bookkeeping
    def mark_index_status(
        self,
        meeting_id: str,
        status: IndexStatus,
        *,
        fingerprint: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Record indexing progress. Never raises - bookkeeping must not be able
        to fail a meeting that is otherwise fine."""
        if not _index_columns_available:
            return

        changes: Dict[str, Any] = {"index_status": status.value, "index_error": error}
        if status is IndexStatus.INDEXED:
            changes["indexed_at"] = utc_now_iso()
            changes["knowledge_fingerprint"] = fingerprint
        elif fingerprint is not None:
            changes["knowledge_fingerprint"] = fingerprint

        try:
            db.run(
                lambda: db.table(MEETINGS_TABLE)
                .update(changes)
                .eq("id", meeting_id)
                .execute(),
                action="meetings.index_status",
            )
        except AppError as exc:
            if _is_missing_index_column(exc):
                _disable_index_columns()
                return
            logger.warning(
                "Could not record index status for meeting %s: %s", meeting_id, exc.code
            )

    def get_index_state(self, meeting_id: str) -> Dict[str, Any]:
        """Stored index status and fingerprint, or empty on a pre-migration DB."""
        if not _index_columns_available:
            return {}
        try:
            response = db.run(
                lambda: db.table(MEETINGS_TABLE)
                .select(",".join(("id",) + INDEX_COLUMNS))
                .eq("id", meeting_id)
                .limit(1)
                .execute(),
                action="meetings.index_state",
            )
        except AppError as exc:
            if _is_missing_index_column(exc):
                _disable_index_columns()
                return {}
            raise
        rows = response.data or []
        return rows[0] if rows else {}

    @property
    def tracks_index_status(self) -> bool:
        """False when migration 002 has not been run."""
        return _index_columns_available


# ------------------------------------------------------------------ helpers
def _is_missing_index_column(error: AppError) -> bool:
    """True when Supabase rejected the query because migration 002 is missing."""
    text = f"{error.internal or ''} {error.message}".lower()
    if not any(column in text for column in INDEX_COLUMNS):
        return False
    return "could not find" in text or "does not exist" in text or "pgrst" in text


def _disable_index_columns() -> None:
    global _index_columns_available
    if _index_columns_available:
        _index_columns_available = False
        logger.warning(
            "Meeting index columns are missing, so indexing status will not be "
            "recorded. Run database/migrations/002_add_knowledge_index_columns.sql "
            "in the Supabase SQL editor to enable it. Search still works."
        )


def reset_index_column_support() -> None:
    """Re-enable column detection. Used by tests."""
    global _index_columns_available
    _index_columns_available = True
