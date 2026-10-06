"""Zoom integration service."""
import base64
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

class ZoomService:
    def __init__(self, meeting_service: Optional[MeetingService] = None):
        self._settings = get_settings()
        self._meeting_service = meeting_service or MeetingService()
        self._meetings = MeetingRepository()
        
    def _check_configured(self):
        if not self._settings.zoom_configured:
            raise AppError(
                "Zoom integration is not configured", 
                status_code=503, 
                code="INTEGRATION_NOT_CONFIGURED"
            )

    async def get_access_token(self) -> str:
        self._check_configured()
        
        url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={self._settings.zoom_account_id}"
        
        auth_string = f"{self._settings.zoom_client_id}:{self._settings.zoom_client_secret}"
        auth_bytes = auth_string.encode("ascii")
        auth_base64 = base64.b64encode(auth_bytes).decode("ascii")
        
        headers = {
            "Authorization": f"Basic {auth_base64}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers)
            
            if response.status_code != 200:
                raise AppError(
                    f"Failed to authenticate with Zoom: {response.text}", 
                    status_code=401, 
                    code="ZOOM_AUTH_FAILED"
                )
                
            data = response.json()
            return data["access_token"]
            
    async def list_recordings(self) -> List[RecordingItem]:
        token = await self.get_access_token()
        
        headers = {
            "Authorization": f"Bearer {token}"
        }
        
        url = "https://api.zoom.us/v2/users/me/recordings"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            
            if response.status_code != 200:
                raise AppError(
                    f"Failed to fetch Zoom recordings: {response.text}", 
                    status_code=response.status_code, 
                    code="ZOOM_API_ERROR"
                )
                
            data = response.json()
            
            recordings = []
            for meeting in data.get("meetings", []):
                for file in meeting.get("recording_files", []):
                    if file.get("file_type") in ("MP4", "M4A", "MP3"):
                        recordings.append(RecordingItem(
                            id=file.get("id"),
                            uuid=meeting.get("uuid"),
                            topic=meeting.get("topic", "Zoom Meeting"),
                            start_time=meeting.get("start_time", ""),
                            duration=meeting.get("duration", 0),
                            download_url=file.get("download_url"),
                            file_type=file.get("file_type"),
                            file_extension=file.get("file_extension", "").lower(),
                            file_size=file.get("file_size", 0)
                        ))
            return recordings
            
    async def import_recording(self, recording_id: str, download_url: str, topic: str, file_ext: str) -> str:
        from app.repositories import supabase_client as db
        
        response = db.run(
            lambda: db.table("meetings").select("id").eq("external_source", "zoom").eq("external_source_id", recording_id).execute(),
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
                            f"Failed to download Zoom recording: {resp.status_code}", 
                            status_code=502, 
                            code="DOWNLOAD_FAILED"
                        )
                    
                    for chunk in resp.iter_bytes(chunk_size=8192):
                        tmp.write(chunk)
                        
        try:
            with open(tmp_path, "rb") as f:
                filename = f"{topic}.{file_ext}" if topic else f"zoom_recording.{file_ext}"
                meeting = self._meeting_service.upload(f, filename, topic)
                
                db.run(
                    lambda: db.table("meetings").update({
                        "external_source": "zoom",
                        "external_source_id": recording_id
                    }).eq("id", meeting.id).execute(),
                    action="meetings.update_external"
                )
                
                return meeting.id
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
