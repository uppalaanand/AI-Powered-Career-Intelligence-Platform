"""Persistence for the `meetings` table."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import get_logger
from app.models.enums import ProcessingStatus
from app.repositories import supabase_client as db
from app.schemas.meeting import MeetingCreate
from app.utils.errors import MeetingNotFoundError
from app.utils.timefmt import utc_now_iso

logger = get_logger(__name__)

TABLE = "meetings"

# Columns fetched for list views. Keeping the transcript text out of list
# queries keeps the dashboard fast on long meetings.
LIST_COLUMNS = (
    "id,title,original_filename,file_extension,media_kind,file_size_bytes,"
    "duration_seconds,status,error_code,error_message,transcript_word_count,"
    "created_at,updated_at"
)


class MeetingRepository:
    def create(self, payload: MeetingCreate) -> Dict[str, Any]:
        record = {
            "title": payload.title,
            "original_filename": payload.original_filename,
            "file_extension": payload.file_extension,
            "media_kind": payload.media_kind.value,
            "mime_type": payload.mime_type,
            "file_size_bytes": payload.file_size_bytes,
            "duration_seconds": payload.duration_seconds,
            "media_metadata": payload.media_metadata.model_dump() if payload.media_metadata else None,
            "status": ProcessingStatus.UPLOADED.value,
        }
        response = db.run(
            lambda: db.table(TABLE).insert(record).execute(), action="meetings.insert"
        )
        rows = response.data or []
        if not rows:
            raise MeetingNotFoundError("The meeting could not be created.")
        logger.info("Created meeting %s (%s)", rows[0].get("id"), payload.original_filename)
        return rows[0]

    def get(self, meeting_id: str) -> Optional[Dict[str, Any]]:
        response = db.run(
            lambda: db.table(TABLE).select("*").eq("id", meeting_id).limit(1).execute(),
            action="meetings.get",
        )
        rows = response.data or []
        return rows[0] if rows else None

    def get_or_404(self, meeting_id: str) -> Dict[str, Any]:
        meeting = self.get(meeting_id)
        if not meeting:
            raise MeetingNotFoundError(details={"meeting_id": meeting_id})
        return meeting

    def list(self, *, limit: int = 50, offset: int = 0,
             status: Optional[ProcessingStatus] = None) -> List[Dict[str, Any]]:
        def query() -> Any:
            builder = db.table(TABLE).select(LIST_COLUMNS)
            if status:
                builder = builder.eq("status", status.value)
            return (
                builder.order("created_at", desc=True)
                .range(offset, offset + max(0, limit - 1))
                .execute()
            )

        return db.run(query, action="meetings.list").data or []

    def count(self, status: Optional[ProcessingStatus] = None) -> int:
        def query() -> Any:
            builder = db.table(TABLE).select("id", count="exact")
            if status:
                builder = builder.eq("status", status.value)
            return builder.execute()

        response = db.run(query, action="meetings.count")
        return response.count or 0

    def update(self, meeting_id: str, changes: Dict[str, Any]) -> Dict[str, Any]:
        payload = {**changes, "updated_at": utc_now_iso()}
        response = db.run(
            lambda: db.table(TABLE).update(payload).eq("id", meeting_id).execute(),
            action="meetings.update",
        )
        rows = response.data or []
        if not rows:
            raise MeetingNotFoundError(details={"meeting_id": meeting_id})
        return rows[0]

    def set_status(
        self,
        meeting_id: str,
        status: ProcessingStatus,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Move a meeting to a new state. Clears the previous error unless failing."""
        changes: Dict[str, Any] = {
            "status": status.value,
            "error_code": error_code,
            "error_message": error_message,
        }
        return self.update(meeting_id, changes)

    def save_transcript(
        self,
        meeting_id: str,
        *,
        text: str,
        language: Optional[str],
        word_count: int,
        model: Optional[str],
        backend: Optional[str],
        duration_seconds: Optional[float],
    ) -> Dict[str, Any]:
        changes = {
            "transcript_text": text,
            "transcript_language": language,
            "transcript_word_count": word_count,
            "transcript_model": model,
            "transcript_backend": backend,
            "transcript_generated_at": utc_now_iso(),
        }
        if duration_seconds:
            changes["duration_seconds"] = duration_seconds
        return self.update(meeting_id, changes)

    def delete(self, meeting_id: str) -> None:
        """Remove a meeting. Child rows disappear via ON DELETE CASCADE."""
        db.run(
            lambda: db.table(TABLE).delete().eq("id", meeting_id).execute(),
            action="meetings.delete",
        )

    def status_counts(self) -> Dict[str, int]:
        response = db.run(
            lambda: db.table(TABLE).select("status").execute(), action="meetings.status_counts"
        )
        counts: Dict[str, int] = {}
        for row in response.data or []:
            key = row.get("status") or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def total_duration_seconds(self) -> float:
        response = db.run(
            lambda: db.table(TABLE).select("duration_seconds").execute(),
            action="meetings.duration_sum",
        )
        return float(sum((row.get("duration_seconds") or 0) for row in (response.data or [])))
