"""Persistence for transcript segments and meeting intelligence.

Writes are *replace-by-meeting* rather than blind inserts: re-running
transcription or analysis for the same meeting overwrites that meeting's rows
instead of piling up duplicate action items, participants and key points.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import get_logger
from app.repositories import supabase_client as db
from app.utils.errors import AppError
from app.schemas.intelligence import MeetingIntelligence
from app.schemas.meeting import TranscriptSegment
from app.utils.timefmt import utc_now_iso

logger = get_logger(__name__)

SEGMENTS_TABLE = "transcript_segments"
PARTICIPANTS_TABLE = "participants"
ACTION_ITEMS_TABLE = "action_items"
DECISIONS_TABLE = "decisions"
KEY_POINTS_TABLE = "key_points"
SUMMARY_TABLE = "meeting_summaries"

#: ``meeting_summaries.provider`` was added with the multi-model fallback (see
#: database/migrations/001_add_provider_to_meeting_summaries.sql). Installations
#: that have not run the migration keep working: the first insert that trips over
#: the missing column is retried without it, and the flag below stops us probing
#: again for the rest of the process.
_provider_column_available = True

# Supabase/PostgREST payloads are chunked so a two-hour meeting does not become
# one enormous request body.
INSERT_BATCH_SIZE = 250


class TranscriptRepository:
    """`transcript_segments` - the timeline view of a transcript."""

    def replace_segments(self, meeting_id: str, segments: List[TranscriptSegment]) -> int:
        self.delete_for_meeting(meeting_id)
        if not segments:
            return 0

        rows = [
            {
                "meeting_id": meeting_id,
                "segment_index": segment.segment_index,
                "start_time": round(float(segment.start_time), 3),
                "end_time": round(float(segment.end_time), 3),
                "speaker": segment.speaker,
                "text": segment.text,
                "confidence": segment.confidence,
            }
            for segment in segments
        ]

        inserted = 0
        for start in range(0, len(rows), INSERT_BATCH_SIZE):
            batch = rows[start : start + INSERT_BATCH_SIZE]
            db.run(
                lambda batch=batch: db.table(SEGMENTS_TABLE).insert(batch).execute(),
                action="transcript_segments.insert",
            )
            inserted += len(batch)
        logger.info("Stored %s transcript segments for meeting %s", inserted, meeting_id)
        return inserted

    def list_segments(self, meeting_id: str) -> List[Dict[str, Any]]:
        response = db.run(
            lambda: db.table(SEGMENTS_TABLE)
            .select("*")
            .eq("meeting_id", meeting_id)
            .order("segment_index")
            .execute(),
            action="transcript_segments.list",
        )
        return response.data or []

    def count_segments(self, meeting_id: str) -> int:
        response = db.run(
            lambda: db.table(SEGMENTS_TABLE)
            .select("id", count="exact")
            .eq("meeting_id", meeting_id)
            .execute(),
            action="transcript_segments.count",
        )
        return response.count or 0

    def delete_for_meeting(self, meeting_id: str) -> None:
        db.run(
            lambda: db.table(SEGMENTS_TABLE).delete().eq("meeting_id", meeting_id).execute(),
            action="transcript_segments.delete",
        )


class IntelligenceRepository:
    """Summary, key points, decisions, participants and action items."""

    # ------------------------------------------------------------------ write
    def replace_intelligence(self, meeting_id: str, intelligence: MeetingIntelligence) -> None:
        """Persist a full analysis result, replacing any previous one."""
        self.delete_for_meeting(meeting_id)
        generated_at = utc_now_iso()

        self._insert_summary(
            {
                "meeting_id": meeting_id,
                "summary": intelligence.summary,
                "model": intelligence.model,
                "provider": intelligence.provider,
                "chunk_count": intelligence.chunk_count,
                "generated_at": generated_at,
            }
        )

        if intelligence.key_points:
            rows = [
                {"meeting_id": meeting_id, "position": index, "text": point.text}
                for index, point in enumerate(intelligence.key_points)
            ]
            db.run(
                lambda: db.table(KEY_POINTS_TABLE).insert(rows).execute(),
                action="key_points.insert",
            )

        if intelligence.decisions:
            rows = [
                {
                    "meeting_id": meeting_id,
                    "position": index,
                    "text": decision.text,
                    "context": decision.context,
                }
                for index, decision in enumerate(intelligence.decisions)
            ]
            db.run(
                lambda: db.table(DECISIONS_TABLE).insert(rows).execute(),
                action="decisions.insert",
            )

        # Participants first: action items reference them by normalized name.
        participant_ids: Dict[str, str] = {}
        if intelligence.participants:
            rows = [
                {
                    "meeting_id": meeting_id,
                    "name": participant.name,
                    "normalized_name": participant.normalized_name or participant.name.lower(),
                    "role": participant.role,
                    "is_unknown": participant.is_unknown,
                    "mention_count": participant.mention_count,
                    "aliases": participant.aliases or [],
                }
                for participant in intelligence.participants
            ]
            response = db.run(
                lambda: db.table(PARTICIPANTS_TABLE).insert(rows).execute(),
                action="participants.insert",
            )
            for row in response.data or []:
                participant_ids[str(row.get("normalized_name"))] = str(row.get("id"))

        if intelligence.action_items:
            rows = []
            for index, item in enumerate(intelligence.action_items):
                normalized_assignee = (item.assigned_to or "").strip().lower()
                rows.append(
                    {
                        "meeting_id": meeting_id,
                        "position": index,
                        "task": item.task,
                        "assigned_to": item.assigned_to,
                        "participant_id": participant_ids.get(normalized_assignee),
                        "deadline": item.deadline,
                        "priority": item.priority.value,
                        "status": item.status.value,
                        "context": item.context,
                    }
                )
            db.run(
                lambda: db.table(ACTION_ITEMS_TABLE).insert(rows).execute(),
                action="action_items.insert",
            )

        logger.info(
            "Stored intelligence for meeting %s: %s key points, %s decisions, "
            "%s participants, %s action items",
            meeting_id,
            len(intelligence.key_points),
            len(intelligence.decisions),
            len(intelligence.participants),
            len(intelligence.action_items),
        )

    def _insert_summary(self, row: Dict[str, Any]) -> None:
        """Insert the summary row, degrading gracefully on a pre-migration schema."""
        global _provider_column_available

        payload = dict(row)
        if not _provider_column_available:
            payload.pop("provider", None)

        try:
            db.run(
                lambda: db.table(SUMMARY_TABLE).insert(payload).execute(),
                action="meeting_summaries.insert",
            )
            return
        except AppError as exc:
            if "provider" not in payload or not _is_missing_column(exc, "provider"):
                raise

        _provider_column_available = False
        logger.warning(
            "meeting_summaries.provider is missing; storing the summary without the "
            "provider name. Run database/migrations/001_add_provider_to_meeting_summaries.sql "
            "to record which AI provider produced each analysis."
        )
        payload.pop("provider", None)
        db.run(
            lambda: db.table(SUMMARY_TABLE).insert(payload).execute(),
            action="meeting_summaries.insert",
        )

    # ------------------------------------------------------------------- read
    def get_intelligence(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        summary_rows = db.run(
            lambda: db.table(SUMMARY_TABLE)
            .select("*")
            .eq("meeting_id", meeting_id)
            .limit(1)
            .execute(),
            action="meeting_summaries.get",
        ).data or []
        if not summary_rows:
            return None

        summary = summary_rows[0]
        return {
            "summary": summary.get("summary") or "",
            "model": summary.get("model"),
            "provider": summary.get("provider"),
            "chunk_count": summary.get("chunk_count") or 1,
            "generated_at": summary.get("generated_at"),
            "key_points": self._ordered(KEY_POINTS_TABLE, meeting_id),
            "decisions": self._ordered(DECISIONS_TABLE, meeting_id),
            "action_items": self._ordered(ACTION_ITEMS_TABLE, meeting_id),
            "participants": self._participants(meeting_id),
        }

    def has_intelligence(self, meeting_id: str) -> bool:
        response = db.run(
            lambda: db.table(SUMMARY_TABLE)
            .select("meeting_id", count="exact")
            .eq("meeting_id", meeting_id)
            .execute(),
            action="meeting_summaries.exists",
        )
        return bool(response.count)

    def _ordered(self, table_name: str, meeting_id: str) -> List[Dict[str, Any]]:
        response = db.run(
            lambda: db.table(table_name)
            .select("*")
            .eq("meeting_id", meeting_id)
            .order("position")
            .execute(),
            action=f"{table_name}.list",
        )
        return response.data or []

    def _participants(self, meeting_id: str) -> List[Dict[str, Any]]:
        response = db.run(
            lambda: db.table(PARTICIPANTS_TABLE)
            .select("*")
            .eq("meeting_id", meeting_id)
            .order("name")
            .execute(),
            action="participants.list",
        )
        return response.data or []

    def delete_for_meeting(self, meeting_id: str) -> None:
        for table_name in (
            SUMMARY_TABLE,
            KEY_POINTS_TABLE,
            DECISIONS_TABLE,
            ACTION_ITEMS_TABLE,
            PARTICIPANTS_TABLE,
        ):
            db.run(
                lambda table_name=table_name: db.table(table_name)
                .delete()
                .eq("meeting_id", meeting_id)
                .execute(),
                action=f"{table_name}.delete",
            )

    # ------------------------------------------------------------ dashboard
    def action_item_counts(self) -> Dict[str, int]:
        response = db.run(
            lambda: db.table(ACTION_ITEMS_TABLE).select("status").execute(),
            action="action_items.counts",
        )
        rows = response.data or []
        open_statuses = {"pending", "in_progress", "blocked"}
        return {
            "total": len(rows),
            "open": sum(1 for row in rows if (row.get("status") or "") in open_statuses),
        }

    def participant_count(self) -> int:
        response = db.run(
            lambda: db.table(PARTICIPANTS_TABLE).select("id", count="exact").execute(),
            action="participants.count",
        )
        return response.count or 0


def _is_missing_column(error: AppError, column: str) -> bool:
    """True when PostgREST rejected the write because `column` does not exist."""
    text = f"{error.internal or ''} {error.message}".lower()
    return column in text and (
        "could not find" in text or "does not exist" in text or "pgrst204" in text
    )
