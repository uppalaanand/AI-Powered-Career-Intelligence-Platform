"""Meeting intelligence controller (Milestone 2)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.schemas.intelligence import IntelligenceResponse, MeetingIntelligence
from app.services.intelligence_service import IntelligenceService


class IntelligenceController:
    def __init__(self, service: Optional[IntelligenceService] = None) -> None:
        self._service = service or IntelligenceService()

    async def analyze(self, meeting_id: str, force: bool = False) -> IntelligenceResponse:
        intelligence: MeetingIntelligence = await self._service.analyze(meeting_id, force=force)
        return IntelligenceResponse(meeting_id=meeting_id, intelligence=intelligence)

    async def get(self, meeting_id: str) -> IntelligenceResponse:
        from starlette.concurrency import run_in_threadpool

        intelligence = await run_in_threadpool(self._service.get, meeting_id)
        return IntelligenceResponse(meeting_id=meeting_id, intelligence=intelligence)

    async def check_llm(self, check_all: bool = False) -> Dict[str, Any]:
        return await self._service.check_llm(check_all=check_all)
