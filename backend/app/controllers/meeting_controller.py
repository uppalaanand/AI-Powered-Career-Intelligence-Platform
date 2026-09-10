"""Meeting controllers.

Routes stay declarative (paths, status codes, OpenAPI metadata); these functions
hold the per-request logic and call services. Whisper and FFmpeg are blocking,
CPU-bound work, so they are dispatched to a worker thread instead of running on
the event loop and freezing every other request.
"""

from __future__ import annotations

from typing import Optional

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.config import get_logger
from app.models.enums import ProcessingStatus
from app.schemas.meeting import (
    DashboardStats,
    MeetingDetail,
    MeetingListResponse,
    Transcript,
    TranscriptResponse,
    UploadResponse,
)
from app.services.export_service import ExportFormat, ExportService
from app.services.meeting_service import MeetingService
from app.utils.errors import BadRequestError

logger = get_logger(__name__)

MAX_PAGE_SIZE = 100


class MeetingController:
    def __init__(
        self,
        service: Optional[MeetingService] = None,
        exporter: Optional[ExportService] = None,
    ) -> None:
        self._service = service or MeetingService()
        self._exporter = exporter or ExportService()

    # --------------------------------------------------------------- upload
    async def upload(self, file: UploadFile, title: Optional[str] = None) -> UploadResponse:
        if file is None or not file.filename:
            raise BadRequestError("No file was included in the request.")

        meeting = await run_in_threadpool(
            self._service.upload, file.file, file.filename, title
        )
        return UploadResponse(
            meeting=meeting,
            message=f"'{meeting.original_filename}' uploaded and validated. Ready to transcribe.",
        )

    # ----------------------------------------------------------- transcribe
    async def transcribe(self, meeting_id: str, force: bool = False) -> TranscriptResponse:
        transcript = await run_in_threadpool(self._service.transcribe, meeting_id, force=force)
        return TranscriptResponse(
            meeting_id=meeting_id,
            status=ProcessingStatus.COMPLETED,
            transcript=transcript,
        )

    # -------------------------------------------------------------- queries
    async def get(self, meeting_id: str) -> MeetingDetail:
        return await run_in_threadpool(self._service.get_detail, meeting_id)

    async def get_transcript(self, meeting_id: str) -> Transcript:
        return await run_in_threadpool(self._service.get_transcript, meeting_id)

    async def list(
        self, limit: int = 50, offset: int = 0, status: Optional[ProcessingStatus] = None
    ) -> MeetingListResponse:
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        meetings, total = await run_in_threadpool(
            self._service.list_meetings, limit=limit, offset=offset, status=status
        )
        return MeetingListResponse(meetings=meetings, total=total)

    async def dashboard(self) -> DashboardStats:
        return await run_in_threadpool(self._service.dashboard)

    async def rename(self, meeting_id: str, title: str) -> MeetingDetail:
        if not title or not title.strip():
            raise BadRequestError("A meeting title cannot be empty.")
        return await run_in_threadpool(self._service.rename, meeting_id, title)

    async def delete(self, meeting_id: str) -> None:
        await run_in_threadpool(self._service.delete, meeting_id)

    # -------------------------------------------------------------- exports
    async def export(self, meeting_id: str, export_format: ExportFormat) -> tuple[str, str, str]:
        """Return (content, filename, media_type) for a transcript download."""
        meeting = await run_in_threadpool(self._service.get_detail, meeting_id)
        transcript = await run_in_threadpool(self._service.get_transcript, meeting_id)
        content = self._exporter.render(transcript, export_format, meeting.title)
        return (
            content,
            self._exporter.filename(meeting.title, export_format),
            self._exporter.media_type(export_format),
        )
