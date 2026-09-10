"""Transcript validation tests (Milestone 1: transcript must be real before storage)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.schemas.meeting import Transcript, TranscriptSegment
from app.services.transcript_validation_service import TranscriptValidationService
from app.services.transcription_service import build_paragraph_text
from app.utils.errors import EmptyTranscriptError, InvalidTranscriptError


@pytest.fixture
def service() -> TranscriptValidationService:
    return TranscriptValidationService()


def segment(index: int, start: float, end: float, text: str = "Some spoken words.") -> TranscriptSegment:
    return TranscriptSegment(segment_index=index, start_time=start, end_time=end, text=text)


class TestSegmentSchema:
    def test_valid_segment_accepted(self):
        result = segment(0, 0.0, 8.0, "Welcome everyone.")
        assert result.duration == 8.0

    def test_end_before_start_rejected(self):
        with pytest.raises(PydanticValidationError):
            TranscriptSegment(segment_index=0, start_time=10.0, end_time=4.0, text="Backwards.")

    def test_negative_start_rejected(self):
        with pytest.raises(PydanticValidationError):
            TranscriptSegment(segment_index=0, start_time=-1.0, end_time=4.0, text="Negative.")

    def test_blank_segment_text_rejected(self):
        with pytest.raises(PydanticValidationError):
            TranscriptSegment(segment_index=0, start_time=0.0, end_time=1.0, text="   ")


class TestTranscriptValidation:
    def test_valid_transcript_passes(self, service):
        transcript = Transcript(
            meeting_id="m-1",
            text="Welcome everyone. Ravi will handle the API integration.",
            segments=[segment(0, 0, 8, "Welcome everyone."),
                      segment(1, 8, 16, "Ravi will handle the API integration.")],
        )
        report = service.validate(transcript, expected_meeting_id="m-1")
        assert report.is_valid
        assert report.segment_count == 2
        assert report.word_count == 8
        assert report.duration_seconds == 16.0

    def test_empty_transcript_rejected(self, service):
        transcript = Transcript(meeting_id="m-1", text="   ")
        with pytest.raises(EmptyTranscriptError) as exc:
            service.validate(transcript, expected_meeting_id="m-1")
        assert exc.value.code == "EMPTY_TRANSCRIPT"

    def test_transcript_belonging_to_another_meeting_rejected(self, service):
        transcript = Transcript(meeting_id="wrong-meeting", text="Some real content here.")
        with pytest.raises(InvalidTranscriptError) as exc:
            service.validate(transcript, expected_meeting_id="m-1")
        assert exc.value.details["expected"] == "m-1"

    def test_missing_segments_warns_but_passes(self, service):
        transcript = Transcript(meeting_id="m-1", text="Text without any timed segments at all.")
        report = service.validate(transcript, expected_meeting_id="m-1")
        assert report.is_valid
        assert any("timeline" in warning for warning in report.warnings)

    def test_unrealistically_long_segment_rejected(self, service):
        transcript = Transcript(
            meeting_id="m-1",
            text="Broken timestamps.",
            segments=[segment(0, 0, 7200, "Broken timestamps.")],
        )
        with pytest.raises(InvalidTranscriptError):
            service.validate(transcript, expected_meeting_id="m-1")

    def test_overlapping_segments_warn(self, service):
        transcript = Transcript(
            meeting_id="m-1",
            text="One two three four.",
            segments=[segment(0, 0, 10, "One two."), segment(1, 2, 12, "Three four.")],
        )
        report = service.validate(transcript, expected_meeting_id="m-1")
        assert report.is_valid
        assert any("overlap" in warning for warning in report.warnings)

    def test_short_coverage_of_long_recording_warns(self, service):
        transcript = Transcript(
            meeting_id="m-1",
            text="Only a little speech in a long recording here.",
            segments=[segment(0, 0, 10, "Only a little speech in a long recording here.")],
        )
        report = service.validate(transcript, expected_meeting_id="m-1", media_duration=600)
        assert any("silent" in warning for warning in report.warnings)


class TestParagraphBuilding:
    def test_segments_join_into_paragraph_text(self):
        text = build_paragraph_text([
            segment(0, 0, 5, "Welcome everyone."),
            segment(1, 5, 10, "Today we discuss the launch."),
        ])
        assert text == "Welcome everyone. Today we discuss the launch."

    def test_long_pause_starts_a_new_paragraph(self):
        text = build_paragraph_text([
            segment(0, 0, 5, "First topic."),
            segment(1, 30, 35, "Completely different topic."),
        ])
        assert "\n\n" in text

    def test_no_segments_returns_empty_string(self):
        assert build_paragraph_text([]) == ""
