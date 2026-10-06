from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class RecordingItem(BaseModel):
    id: str
    uuid: str
    topic: str
    start_time: str
    duration: int
    download_url: str
    file_type: str
    file_extension: str
    file_size: int

class IntegrationRecordingsResponse(BaseModel):
    source: str
    recordings: List[RecordingItem]

class IntegrationImportResponse(BaseModel):
    meeting_id: str
    status: str
    message: str
