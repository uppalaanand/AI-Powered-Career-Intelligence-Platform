"""Transcript validation - the gate between Whisper and the database.

Whisper always returns *something*. These checks decide whether that something
is a usable transcript, so an empty or nonsensical result becomes an honest
error instead of a stored empty record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.config import get_logger
from app.schemas.meeting import Transcript, TranscriptSegment
from app.utils.errors import EmptyTranscriptError, InvalidTranscriptError

logger = get_logger(__name__)

MIN_WORDS = 1
MIN_CHARACTERS = 2
# Segments longer than this indicate a broken timestamp rather than real speech.
MAX_SEGMENT_SECONDS = 3600.0


@dataclass
class TranscriptValidationReport:
    is_valid: bool
    word_count: int
    segment_count: int
    duration_seconds: float
    warnings: List[str] = field(default_factory=list)


class TranscriptValidationService:
    def validate(
        self,
        transcript: Transcript,
        *,
        expected_meeting_id: Optional[str] = None,
        media_duration: Optional[float] = None,
    ) -> TranscriptValidationReport:
        warnings: List[str] = []

        # 1. Ownership: never attach a transcript to the wrong meeting.
        if expected_meeting_id and transcript.meeting_id != expected_meeting_id:
            raise InvalidTranscriptError(
                "The transcript does not belong to this meeting.",
                details={"expected": expected_meeting_id, "received": transcript.meeting_id},
            )

        # 2. Presence.
        text = (transcript.text or "").strip()
        if not text or len(text) < MIN_CHARACTERS:
            raise EmptyTranscriptError(
                "No speech was detected in this recording. Check that the audio track "
                "contains speech and is not silent."
            )

        words = text.split()
        if len(words) < MIN_WORDS:
            raise EmptyTranscriptError(
                "The transcript contains no readable words.", details={"word_count": len(words)}
            )

        # 3. Segments and timestamps.
        segments = transcript.segments or []
        if not segments:
            warnings.append(
                "No timed segments were produced, so the timeline view will be unavailable."
            )
        else:
            self._validate_segments(segments, warnings, media_duration)

        duration = segments[-1].end_time if segments else (transcript.duration_seconds or 0.0)

        # 4. Sanity signals - warn, do not block.
        if len(words) < 5:
            warnings.append("The transcript is very short; the recording may be mostly silent.")
        if media_duration and duration and duration < media_duration * 0.5:
            warnings.append(
                "Transcribed speech covers less than half of the recording, which usually "
                "means long silent stretches."
            )

        report = TranscriptValidationReport(
            is_valid=True,
            word_count=len(words),
            segment_count=len(segments),
            duration_seconds=round(duration, 2),
            warnings=warnings,
        )
        if warnings:
            logger.warning("Transcript validation warnings: %s", "; ".join(warnings))
        return report

    @staticmethod
    def _validate_segments(
        segments: List[TranscriptSegment],
        warnings: List[str],
        media_duration: Optional[float],
    ) -> None:
        previous_end = -1.0
        overlaps = 0

        for index, segment in enumerate(segments):
            if segment.end_time < segment.start_time:
                raise InvalidTranscriptError(
                    f"Segment {index} ends before it starts.",
                    details={"segment_index": index,
                             "start_time": segment.start_time, "end_time": segment.end_time},
                )
            if segment.start_time < 0:
                raise InvalidTranscriptError(
                    f"Segment {index} has a negative timestamp.",
                    details={"segment_index": index},
                )
            if (segment.end_time - segment.start_time) > MAX_SEGMENT_SECONDS:
                raise InvalidTranscriptError(
                    f"Segment {index} is unrealistically long, so its timestamps are unreliable.",
                    details={"segment_index": index},
                )
            if segment.start_time < previous_end - 0.5:
                overlaps += 1
            previous_end = segment.end_time

        if overlaps:
            warnings.append(f"{overlaps} segment(s) overlap the previous one.")

        if media_duration and segments[-1].end_time > media_duration + 5:
            warnings.append(
                "The last segment ends after the recording does; timestamps may be approximate."
            )
