"""Semantic search endpoint (Milestone 3, Task 4)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.controllers.search_controller import SearchController
from app.schemas.common import ErrorResponse
from app.schemas.search import SearchRequest
from app.utils.responses import success_payload

router = APIRouter(prefix="/meetings", tags=["Semantic search"])


def get_controller() -> SearchController:
    return SearchController()


@router.post(
    "/search",
    summary="Search historical meetings by meaning",
    description=(
        "Natural-language search across every indexed meeting.\n\n"
        "This is **semantic**, not keyword, search: the query is embedded into the "
        "same vector space as the meeting passages, so *'which meeting discussed the "
        "database migration?'* matches a meeting that talked about *'moving the "
        "Postgres schema'* without sharing a keyword.\n\n"
        "Costs one embedding request, one vector search and one database read - "
        "**no LLM is called**, which keeps it fast and free of AI quota. Ask "
        "`/api/meetings/ask` when you want an answer rather than a list of meetings.\n\n"
        "Results are grouped by meeting and ordered by the best matching passage. "
        "Optional `filters` (meeting, source type, date range, assignee) are applied "
        "inside the vector search itself."
    ),
    responses={
        422: {"model": ErrorResponse, "description": "Empty or invalid query"},
        503: {
            "model": ErrorResponse,
            "description": "Embeddings or the vector index are not configured",
        },
    },
)
async def search_meetings(
    payload: SearchRequest = Body(
        ...,
        examples=[{"query": "Which meeting discussed the database migration?", "top_k": 8}],
    ),
    controller: SearchController = Depends(get_controller),
):
    result = await controller.search(
        payload.query, top_k=payload.top_k, filters=payload.filters
    )
    return success_payload(
        result.model_dump(mode="json"),
        result.message or f"Found {len(result.results)} relevant meeting(s).",
    )
