"""Meeting orchestration: upload -> validate -> FFmpeg -> Whisper -> database.

This service owns the *sequence*. Each step is delegated to a focused service,
and the meeting's status is advanced in the database before every step so the
frontend's processing screen shows real progress rather than a fake animation.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional

from app.config import get_logger, get_settings
from app.models.enums import MediaKind, ProcessingStatus
from app.repositories.intelligence_repository import IntelligenceRepository, TranscriptRepository
from app.repositories.meeting_repository import MeetingRepository
from app.schemas.meeting import (
    DashboardStats,
    MeetingCreate,
    MeetingDetail,
    MeetingSummaryRow,
    Transcript,
    TranscriptSegment,
)
from app.services.audio_service import AudioService
from app.services.file_validation_service import FileValidationService
from app.services.storage_service import MediaStore
from app.services.transcript_validation_service import TranscriptValidationService
from app.services.transcription_service import TranscriptionService
from app.utils.errors import AppError, ConflictError, TranscriptNotFoundError
from app.utils.text import truncate

logger = get_logger(__name__)


class MeetingService:
    def __init__(
        self,
        meetings: Optional[MeetingRepository] = None,
        transcripts: Optional[TranscriptRepository] = None,
        intelligence: Optional[IntelligenceRepository] = None,
        validator: Optional[FileValidationService] = None,
        audio: Optional[AudioService] = None,
        transcription: Optional[TranscriptionService] = None,
        transcript_validator: Optional[TranscriptValidationService] = None,
        store: Optional[MediaStore] = None,
    ) -> None:
        self._settings = get_settings()
        self._meetings = meetings or MeetingRepository()
        self._transcripts = transcripts or TranscriptRepository()
        self._intelligence = intelligence or IntelligenceRepository()
        self._validator = validator or FileValidationService()
        self._audio = audio or AudioService()
        self._transcription = transcription
        self._transcript_validator = transcript_validator or TranscriptValidationService()
        self._store = store or MediaStore()

    def _transcriber(self) -> TranscriptionService:
        # Built lazily so importing this module never loads Whisper weights.
        if self._transcription is None:
            self._transcription = TranscriptionService()
        return self._transcription

    # --------------------------------------------------------------- upload
    def upload(self, stream: BinaryIO, filename: str, title: Optional[str] = None) -> MeetingDetail:
        """Validate an uploaded recording and create its meeting row."""
        staging = self._store.create_staging_dir()
        try:
            stored_path = self._store.write_stream(stream, staging, filename)
            validated = self._validator.validate(stored_path, filename)

            meeting_row = self._meetings.create(
                MeetingCreate(
                    title=(title or "").strip() or _title_from_filename(validated.safe_filename),
                    original_filename=validated.safe_filename,
                    file_extension=validated.extension,
                    media_kind=validated.media_kind,
                    mime_type=validated.mime_type,
                    file_size_bytes=validated.size_bytes,
                    duration_seconds=validated.metadata.duration_seconds,
                    media_metadata=validated.metadata,
                )
            )
            meeting_id = str(meeting_row["id"])
            self._store.promote(stored_path, meeting_id)
            return self.get_detail(meeting_id)
        except Exception:
            self._store.discard_staging(staging)
            raise

    # ---------------------------------------------------------- transcribe
    def transcribe(self, meeting_id: str, *, force: bool = False) -> Transcript:
        """Run FFmpeg + Whisper for a meeting and store the transcript."""
        meeting = self._meetings.get_or_404(meeting_id)
        status = ProcessingStatus(meeting.get("status", ProcessingStatus.UPLOADED.value))

        if status.is_busy and not force:
            raise ConflictError(
                "This meeting is already being processed.", details={"status": status.value}
            )
        if meeting.get("transcript_text") and not force:
            logger.info("Meeting %s already has a transcript; returning it.", meeting_id)
            return self.get_transcript(meeting_id)

        media_path = self._store.find_media(meeting_id)
        if media_path is None or not media_path.exists():
            self._fail(meeting_id, "MEDIA_UNAVAILABLE",
                       "The uploaded recording is no longer available on the server. "
                       "Upload the file again to transcribe it.")
            raise ConflictError(
                "The uploaded recording is no longer available on the server. "
                "Upload the file again to transcribe it.",
                code="MEDIA_UNAVAILABLE",
            )

        try:
            # 1. Re-validate: the file may have changed since upload.
            self._meetings.set_status(meeting_id, ProcessingStatus.VALIDATING)
            metadata = self._validator.validate_media_streams(media_path)

            # 2. FFmpeg: extract and normalise audio.
            self._meetings.set_status(meeting_id, ProcessingStatus.AUDIO_PROCESSING)
            audio_path = self._store.working_audio_path(meeting_id)
            self._audio.extract_audio(media_path, audio_path)

            # 3. Whisper.
            self._meetings.set_status(meeting_id, ProcessingStatus.TRANSCRIBING)
            result = self._transcriber().transcribe(audio_path)

            transcript = Transcript(
                meeting_id=meeting_id,
                text=result.text,
                segments=result.segments,
                language=result.language,
                duration_seconds=result.duration or metadata.duration_seconds,
                model=result.model,
                backend=result.backend,
            )

            # 4. Validate before anything is stored.
            report = self._transcript_validator.validate(
                transcript,
                expected_meeting_id=meeting_id,
                media_duration=metadata.duration_seconds,
            )
            self._meetings.set_status(meeting_id, ProcessingStatus.TRANSCRIPT_VALIDATED)

            # 5. Persist.
            self._meetings.set_status(meeting_id, ProcessingStatus.PERSISTING)
            self._meetings.save_transcript(
                meeting_id,
                text=transcript.text,
                language=transcript.language,
                word_count=report.word_count,
                model=result.model,
                backend=result.backend,
                duration_seconds=transcript.duration_seconds,
            )
            self._transcripts.replace_segments(meeting_id, transcript.segments)
            self._meetings.set_status(meeting_id, ProcessingStatus.COMPLETED)

            logger.info(
                "Transcription complete for %s: %s words, %s segments in %.1fs",
                meeting_id, report.word_count, report.segment_count, result.elapsed_seconds,
            )
            return transcript

        except AppError as exc:
            self._fail(meeting_id, exc.code, exc.message)
            raise
        except Exception as exc:  # noqa: BLE001 - unexpected failures still update status
            logger.exception("Unexpected transcription failure for %s", meeting_id)
            self._fail(meeting_id, "TRANSCRIPTION_FAILED",
                       "Transcription failed unexpectedly. Check the backend logs.")
            raise
        finally:
            self._store.cleanup(meeting_id)

    # -------------------------------------------------------------- queries
    def get_detail(self, meeting_id: str) -> MeetingDetail:
        row = self._meetings.get_or_404(meeting_id)
        segment_count = self._transcripts.count_segments(meeting_id) if row.get("transcript_text") else 0
        has_intelligence = self._intelligence.has_intelligence(meeting_id)
        return _to_detail(row, segment_count=segment_count, has_intelligence=has_intelligence)

    def get_transcript(self, meeting_id: str) -> Transcript:
        row = self._meetings.get_or_404(meeting_id)
        text = row.get("transcript_text")
        if not text:
            raise TranscriptNotFoundError(details={"meeting_id": meeting_id})

        segments = [
            TranscriptSegment(
                segment_index=segment.get("segment_index", index),
                start_time=float(segment.get("start_time") or 0),
                end_time=float(segment.get("end_time") or 0),
                text=segment.get("text") or "",
                speaker=segment.get("speaker"),
                confidence=segment.get("confidence"),
            )
            for index, segment in enumerate(self._transcripts.list_segments(meeting_id))
            if (segment.get("text") or "").strip()
        ]

        return Transcript(
            meeting_id=meeting_id,
            text=text,
            segments=segments,
            language=row.get("transcript_language"),
            duration_seconds=row.get("duration_seconds"),
            model=row.get("transcript_model"),
            backend=row.get("transcript_backend"),
            word_count=row.get("transcript_word_count") or len(text.split()),
            created_at=_parse_datetime(row.get("transcript_generated_at")),
        )

    def list_meetings(
        self, *, limit: int = 50, offset: int = 0, status: Optional[ProcessingStatus] = None
    ) -> tuple[List[MeetingSummaryRow], int]:
        rows = self._meetings.list(limit=limit, offset=offset, status=status)
        total = self._meetings.count(status)
        return [_to_summary(row) for row in rows], total

    def dashboard(self) -> DashboardStats:
        counts = self._meetings.status_counts()
        recent_rows = self._meetings.list(limit=5)
        action_counts = self._intelligence.action_item_counts()

        busy = sum(
            count for status, count in counts.items()
            if status not in (ProcessingStatus.COMPLETED.value, ProcessingStatus.FAILED.value)
            and status != ProcessingStatus.UPLOADED.value
        )

        return DashboardStats(
            total_meetings=sum(counts.values()),
            completed_meetings=counts.get(ProcessingStatus.COMPLETED.value, 0),
            processing_meetings=busy,
            failed_meetings=counts.get(ProcessingStatus.FAILED.value, 0),
            total_action_items=action_counts.get("total", 0),
            open_action_items=action_counts.get("open", 0),
            total_participants=self._intelligence.participant_count(),
            total_transcribed_minutes=round(self._meetings.total_duration_seconds() / 60, 1),
            recent_meetings=[_to_summary(row) for row in recent_rows],
        )

    def delete(self, meeting_id: str) -> None:
        self._meetings.get_or_404(meeting_id)
        self._store.cleanup(meeting_id)
        self._meetings.delete(meeting_id)

    def rename(self, meeting_id: str, title: str) -> MeetingDetail:
        self._meetings.get_or_404(meeting_id)
        self._meetings.update(meeting_id, {"title": title.strip()})
        return self.get_detail(meeting_id)

    # ------------------------------------------------------------- internal
    def _fail(self, meeting_id: str, code: str, message: str) -> None:
        try:
            self._meetings.set_status(
                meeting_id, ProcessingStatus.FAILED,
                error_code=code, error_message=truncate(message, 500),
            )
        except Exception:  # noqa: BLE001 - never mask the original failure
            logger.exception("Could not record failure state for meeting %s", meeting_id)


# ------------------------------------------------------------------ mapping
def _to_summary(row: Dict[str, Any]) -> MeetingSummaryRow:
    return MeetingSummaryRow(
        id=str(row.get("id")),
        title=row.get("title") or "Untitled meeting",
        original_filename=row.get("original_filename") or "",
        media_kind=MediaKind(row.get("media_kind") or MediaKind.AUDIO.value),
        file_size_bytes=row.get("file_size_bytes") or 0,
        duration_seconds=row.get("duration_seconds"),
        status=ProcessingStatus(row.get("status") or ProcessingStatus.UPLOADED.value),
        has_transcript=bool(row.get("transcript_text") or row.get("transcript_word_count")),
        has_intelligence=bool(row.get("has_intelligence")),
        error_code=row.get("error_code"),
        error_message=row.get("error_message"),
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
    )


def _to_detail(row: Dict[str, Any], *, segment_count: int, has_intelligence: bool) -> MeetingDetail:
    summary = _to_summary(row)
    return MeetingDetail(
        **summary.model_dump(),
        file_extension=row.get("file_extension"),
        mime_type=row.get("mime_type"),
        media_metadata=row.get("media_metadata"),
        transcript_language=row.get("transcript_language"),
        transcript_word_count=row.get("transcript_word_count"),
        transcript_model=row.get("transcript_model"),
        segment_count=segment_count,
    ).model_copy(update={"has_intelligence": has_intelligence})


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.replace("_", " ").replace("-", " ").strip()
    return (stem[:120] or "Untitled meeting").title()
