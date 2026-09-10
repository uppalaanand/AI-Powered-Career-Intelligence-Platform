"""Transcription accuracy controller (Milestone 1, task: accuracy testing)."""

from __future__ import annotations

from typing import Optional

from starlette.concurrency import run_in_threadpool

from app.schemas.accuracy import AccuracyRequest, AccuracyResult
from app.services.accuracy_service import AccuracyService
from app.services.meeting_service import MeetingService
from app.utils.errors import BadRequestError, ValidationError


class AccuracyController:
    def __init__(
        self,
        accuracy: Optional[AccuracyService] = None,
        meetings: Optional[MeetingService] = None,
    ) -> None:
        self._accuracy = accuracy or AccuracyService()
        self._meetings = meetings or MeetingService()

    async def compare(self, request: AccuracyRequest) -> AccuracyResult:
        hypothesis = request.hypothesis_transcript

        if not hypothesis and request.meeting_id:
            transcript = await run_in_threadpool(
                self._meetings.get_transcript, request.meeting_id
            )
            hypothesis = transcript.text

        if not hypothesis or not hypothesis.strip():
            raise BadRequestError(
                "Provide either a transcript to score (hypothesis_transcript) or the "
                "meeting_id of a transcribed meeting."
            )

        try:
            return await run_in_threadpool(
                self._accuracy.compare,
                request.reference_transcript,
                hypothesis,
                target_accuracy=request.target_accuracy,
                ignore_case=request.ignore_case,
                ignore_punctuation=request.ignore_punctuation,
                ignore_filler_words=request.ignore_filler_words,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
