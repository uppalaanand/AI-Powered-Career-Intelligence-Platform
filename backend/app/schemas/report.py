from typing import Dict, Optional
from pydantic import BaseModel

class AnalyticsResponse(BaseModel):
    meeting_duration_seconds: Optional[float] = None
    participant_count: int = 0
    action_item_count: int = 0
    completed_action_items: int = 0
    pending_action_items: int = 0
    decision_count: int = 0
    key_point_count: int = 0
    deadline_count: int = 0
    action_items_by_participant: Dict[str, int]
    priority_distribution: Dict[str, int]
    transcript_word_count: Optional[int] = None
    index_status: Optional[str] = None
