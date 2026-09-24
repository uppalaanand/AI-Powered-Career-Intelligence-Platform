"""Meeting question-answering controller (Milestone 3)."""

from __future__ import annotations

from typing import Optional

from app.schemas.rag import AskResponse
from app.schemas.search import SearchFilters
from app.services.rag_service import RAGService


class RAGController:
    def __init__(self, service: Optional[RAGService] = None) -> None:
        self._service = service or RAGService()

    async def ask(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        filters: Optional[SearchFilters] = None,
    ) -> AskResponse:
        return await self._service.ask(question, top_k=top_k, filters=filters)
