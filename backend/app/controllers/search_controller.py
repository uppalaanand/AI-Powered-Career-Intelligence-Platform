"""Semantic search controller (Milestone 3).

Thin by design, exactly like the existing controllers: it turns a validated
request into a service call and a response model. No Pinecone call, no Supabase
call and no embedding call happens here.
"""

from __future__ import annotations

from typing import Optional

from app.schemas.search import SearchFilters, SearchResponse
from app.services.semantic_search_service import SemanticSearchService


class SearchController:
    def __init__(self, service: Optional[SemanticSearchService] = None) -> None:
        self._service = service or SemanticSearchService()

    async def search(
        self,
        query: str,
        *,
        top_k: Optional[int] = None,
        filters: Optional[SearchFilters] = None,
    ) -> SearchResponse:
        return await self._service.search(query, top_k=top_k, filters=filters)
