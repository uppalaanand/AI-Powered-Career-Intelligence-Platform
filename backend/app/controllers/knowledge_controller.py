"""Knowledge index controller (Milestone 3).

Owns the *management* side of the knowledge repository - building the index for
historical meetings, refreshing one meeting, removing one meeting - while
search and question answering live in their own controllers.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from starlette.concurrency import run_in_threadpool

from app.services.knowledge_index_service import KnowledgeIndexService


class KnowledgeController:
    def __init__(self, service: Optional[KnowledgeIndexService] = None) -> None:
        self._service = service or KnowledgeIndexService()

    async def index_all(
        self, *, limit: Optional[int] = None, force: bool = False
    ) -> Dict[str, Any]:
        result = await self._service.index_all(limit=limit, force=force)
        return result.to_dict()

    async def index_meeting(self, meeting_id: str, *, force: bool = False) -> Dict[str, Any]:
        result = await self._service.index_meeting(meeting_id, force=force)
        return result.to_dict()

    async def delete_meeting(self, meeting_id: str) -> Dict[str, Any]:
        removed = await self._service.delete_meeting(meeting_id)
        return {"meeting_id": meeting_id, "vectors_removed": removed}

    async def status(self) -> Dict[str, Any]:
        # `status()` reads Supabase with the blocking client, so keep it off
        # the event loop exactly as the other read endpoints do.
        return await run_in_threadpool(self._service.status)
