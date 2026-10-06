"""Integration controller."""

from typing import Optional

from app.services.zoom_service import ZoomService
from app.services.google_meet_service import GoogleMeetService
from app.schemas.integration import IntegrationRecordingsResponse, IntegrationImportResponse
from app.utils.errors import AppError

class IntegrationController:
    def __init__(self, zoom_service: Optional[ZoomService] = None, google_meet_service: Optional[GoogleMeetService] = None) -> None:
        self._zoom = zoom_service or ZoomService()
        self._google_meet = google_meet_service or GoogleMeetService()

    async def auth_zoom(self):
        raise AppError(
            "Zoom auth is server-to-server and does not require explicit auth route", 
            status_code=400, 
            code="AUTH_NOT_REQUIRED"
        )

    async def list_zoom_recordings(self) -> IntegrationRecordingsResponse:
        recordings = await self._zoom.list_recordings()
        return IntegrationRecordingsResponse(source="zoom", recordings=recordings)

    async def import_zoom_recording(self, recording_id: str, download_url: str, topic: str, file_ext: str) -> IntegrationImportResponse:
        meeting_id = await self._zoom.import_recording(recording_id, download_url, topic, file_ext)
        return IntegrationImportResponse(meeting_id=meeting_id, status="success", message="Imported successfully")

    async def auth_google_meet(self):
        await self._google_meet.get_access_token()

    async def list_google_meet_recordings(self) -> IntegrationRecordingsResponse:
        recordings = await self._google_meet.list_recordings()
        return IntegrationRecordingsResponse(source="google_meet", recordings=recordings)

    async def import_google_meet_recording(self, recording_id: str, download_url: str, topic: str, file_ext: str) -> IntegrationImportResponse:
        meeting_id = await self._google_meet.import_recording(recording_id, download_url, topic, file_ext)
        return IntegrationImportResponse(meeting_id=meeting_id, status="success", message="Imported successfully")
