"""Meeting and transcript schemas (API layer contract)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import MediaKind, ProcessingStatus


# --------------------------------------------------------------------------
# Media metadata
# --------------------------------------------------------------------------
class MediaMetadata(BaseModel):
    """What ffprobe found inside the uploaded file."""

    duration_seconds: Optional[float] = Field(None, description="Total media duration.")
    container: Optional[str] = Field(None, description="Container format reported by ffprobe.")
    has_audio_stream: bool = Field(False, description="True when a decodable audio stream exists.")
    has_video_stream: bool = False
    audio_codec: Optional[str] = None
    video_codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    bit_rate: Optional[int] = None


# --------------------------------------------------------------------------
# Transcript segments
# --------------------------------------------------------------------------
class TranscriptSegment(BaseModel):
    """One timed chunk of speech."""

    segment_index: int = Field(..., ge=0, description="Zero-based position in the transcript.")
    start_time: float = Field(..., ge=0, description="Segment start, in seconds.")
    end_time: float = Field(..., ge=0, description="Segment end, in seconds.")
    text: str = Field(..., description="Recognised speech for this segment.")
    speaker: Optional[str] = Field(None, description="Speaker label when available.")
    confidence: Optional[float] = Field(None, description="Model confidence, when the backend reports it.")

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("segment text must not be empty")
        return cleaned

    @model_validator(mode="after")
    def _end_after_start(self) -> "TranscriptSegment":
        if self.end_time < self.start_time:
            raise ValueError(
                f"segment {self.segment_index}: end_time ({self.end_time}) "
                f"is before start_time ({self.start_time})"
            )
        return self

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


class Transcript(BaseModel):
    """Full transcript for one meeting."""

    meeting_id: str
    text: str = Field(..., description="Whole transcript as flowing paragraphs.")
    segments: List[TranscriptSegment] = Field(default_factory=list)
    language: Optional[str] = None
    duration_seconds: Optional[float] = None
    model: Optional[str] = Field(None, description="Whisper model that produced this transcript.")
    backend: Optional[str] = None
    word_count: int = 0
    created_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _compute_word_count(self) -> "Transcript":
        if not self.word_count:
            object.__setattr__(self, "word_count", len(self.text.split()))
        return self


# --------------------------------------------------------------------------
# Meetings
# --------------------------------------------------------------------------
class MeetingBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)


class MeetingCreate(MeetingBase):
    """Server-side view of a newly uploaded meeting."""

    original_filename: str
    file_extension: str
    media_kind: MediaKind
    mime_type: Optional[str] = None
    file_size_bytes: int = Field(..., ge=0)
    duration_seconds: Optional[float] = None
    media_metadata: Optional[MediaMetadata] = None


class MeetingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[ProcessingStatus] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class MeetingSummaryRow(BaseModel):
    """Row shape used by list views on the dashboard."""

    id: str
    title: str
    original_filename: str
    media_kind: MediaKind
    file_size_bytes: int = 0
    duration_seconds: Optional[float] = None
    status: ProcessingStatus
    has_transcript: bool = False
    has_intelligence: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MeetingDetail(MeetingSummaryRow):
    file_extension: Optional[str] = None
    mime_type: Optional[str] = None
    media_metadata: Optional[Dict[str, Any]] = None
    transcript_language: Optional[str] = None
    transcript_word_count: Optional[int] = None
    transcript_model: Optional[str] = None
    segment_count: int = 0


class MeetingListResponse(BaseModel):
    meetings: List[MeetingSummaryRow]
    total: int


class DashboardStats(BaseModel):
    total_meetings: int = 0
    completed_meetings: int = 0
    processing_meetings: int = 0
    failed_meetings: int = 0
    total_action_items: int = 0
    open_action_items: int = 0
    total_participants: int = 0
    total_transcribed_minutes: float = 0.0
    recent_meetings: List[MeetingSummaryRow] = Field(default_factory=list)


class UploadResponse(BaseModel):
    meeting: MeetingDetail
    message: str = "Recording uploaded and validated."


class TranscriptResponse(BaseModel):
    meeting_id: str
    status: ProcessingStatus
    transcript: Transcript


class SupportedFormatsResponse(BaseModel):
    audio: List[str]
    video: List[str]
    accept_attribute: str
    max_upload_size_mb: int
