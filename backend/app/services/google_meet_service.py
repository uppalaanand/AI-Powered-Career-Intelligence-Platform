"""Google Meet integration service."""
import httpx
import tempfile
import os
from typing import List, Dict, Any, Optional

from app.config import get_settings, get_logger
from app.utils.errors import AppError, ConflictError
from app.schemas.integration import RecordingItem
from app.services.meeting_service import MeetingService
from app.repositories.meeting_repository import MeetingRepository

logger = get_logger(__name__)

class GoogleMeetService:
    def __init__(self, meeting_service: Optional[MeetingService] = None):
        self._settings = get_settings()
        self._meeting_service = meeting_service or MeetingService()
        self._meetings = MeetingRepository()
        
    def _check_configured(self):
        if not self._settings.google_meet_configured:
            raise AppError(
                "Google Meet integration is not configured", 
                status_code=503, 
                code="INTEGRATION_NOT_CONFIGURED"
            )

    async def get_access_token(self) -> str:
        raise AppError("Google OAuth flow not implemented", status_code=501, code="NOT_IMPLEMENTED")
            
    async def list_recordings(self) -> List[RecordingItem]:
        self._check_configured()
        
        token = await self.get_access_token()
        
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        url = "https://www.googleapis.com/drive/v3/files?q=mimeType='video/mp4'+and+name+contains+'Meet'&fields=files(id,name,createdTime,size)"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            
            if response.status_code != 200:
                raise AppError(
                    f"Failed to fetch Google Meet recordings: {response.text}", 
                    status_code=response.status_code, 
                    code="GOOGLE_API_ERROR"
                )
                
            data = response.json()
            
            recordings = []
            for file in data.get("files", []):
                recordings.append(RecordingItem(
                    id=file.get("id"),
                    uuid=file.get("id"),
                    topic=file.get("name", "Google Meet Recording"),
                    start_time=file.get("createdTime", ""),
                    duration=0,
                    download_url=f"https://www.googleapis.com/drive/v3/files/{file.get('id')}?alt=media",
                    file_type="MP4",
                    file_extension="mp4",
                    file_size=int(file.get("size", 0))
                ))
            return recordings
            
    async def import_recording(self, recording_id: str, download_url: str, topic: str, file_ext: str) -> str:
        from app.repositories import supabase_client as db
        
        response = db.run(
            lambda: db.table("meetings").select("id").eq("external_source", "google_meet").eq("external_source_id", recording_id).execute(),
            action="meetings.check_external"
        )
        if response.data:
            raise ConflictError("Recording has already been imported", code="ALREADY_IMPORTED")
            
        token = await self.get_access_token()
        
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_ext}") as tmp:
            tmp_path = tmp.name
            
            async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
                async with client.stream('GET', download_url, headers=headers) as resp:
                    if resp.status_code != 200:
                        os.unlink(tmp_path)
                        raise AppError(
                            f"Failed to download Google Meet recording: {resp.status_code}", 
                            status_code=502, 
                            code="DOWNLOAD_FAILED"
                        )
                    
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        tmp.write(chunk)
                        
        try:
            with open(tmp_path, "rb") as f:
                filename = f"{topic}.{file_ext}" if topic else f"meet_recording.{file_ext}"
                meeting = self._meeting_service.upload(f, filename, topic)
                
                db.run(
                    lambda: db.table("meetings").update({
                        "external_source": "google_meet",
                        "external_source_id": recording_id
                    }).eq("id", meeting.id).execute(),
                    action="meetings.update_external"
                )
                
                return meeting.id
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
