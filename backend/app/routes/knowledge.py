"""Knowledge index management (Milestone 3, Tasks 1-3).

Indexing is a management action, so it lives on its own ``/knowledge`` resource
rather than being buried in the meeting endpoints. Note that indexing is *not*
something the UI does on every page load - it is explicit, and it is cheap to
repeat because unchanged meetings are skipped without spending embedding quota.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path, Query

from app.controllers.knowledge_controller import KnowledgeController
from app.schemas.common import ErrorResponse
from app.utils.responses import success_payload

router = APIRouter(prefix="/knowledge", tags=["Meeting knowledge index"])


def get_controller() -> KnowledgeController:
    return KnowledgeController()


@router.get(
    "/status",
    summary="Knowledge index configuration and coverage",
    description=(
        "Reports whether embeddings and the vector index are configured, which "
        "model and index are in use, and how many meetings are indexed.\n\n"
        "Makes **no external calls** - it reads configuration and the database "
        "only, so the UI can check readiness without spending any quota."
    ),
)
async def knowledge_status(controller: KnowledgeController = Depends(get_controller)):
    return success_payload(await controller.status(), "Knowledge index status retrieved.")


@router.post(
    "/index",
    summary="Index historical meetings",
    description=(
        "Builds the searchable knowledge repository from meetings **already in "
        "Supabase** - this is how existing, historical meetings become "
        "searchable.\n\n"
        "For each meeting: its transcript, summary, decisions, action items, key "
        "points and participants are turned into knowledge documents, embedded, and "
        "upserted into the vector index under deterministic ids.\n\n"
        "Safe and cheap to re-run: a meeting whose content has not changed since it "
        "was last indexed is skipped **without any embedding request**. Pass "
        "`force: true` only when you deliberately want to re-embed everything."
    ),
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Embeddings or the vector index are not configured",
        }
    },
)
async def index_meetings(
    payload: dict = Body(
        default={},
        examples=[{"limit": 50, "force": False}],
        description="Optional `limit` (meetings to process) and `force` (re-embed unchanged).",
    ),
    controller: KnowledgeController = Depends(get_controller),
):
    limit = payload.get("limit") if isinstance(payload, dict) else None
    force = bool(payload.get("force")) if isinstance(payload, dict) else False
    result = await controller.index_all(
        limit=int(limit) if limit else None, force=force
    )
    return success_payload(
        result,
        f"Indexed {result['indexed']} meeting(s); "
        f"{result['skipped_unchanged']} already up to date.",
    )


@router.post(
    "/meetings/{meeting_id}",
    summary="Index or refresh one meeting",
    description=(
        "Re-reads one meeting from Supabase and refreshes its vectors. Vectors "
        "whose source records no longer exist are removed in the same pass, so "
        "search cannot return something that has been deleted."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Meeting not found"},
        503: {"model": ErrorResponse, "description": "Search is not configured"},
    },
)
async def index_meeting(
    meeting_id: str = Path(..., description="Meeting to index."),
    force: bool = Query(False, description="Re-embed even if the content is unchanged."),
    controller: KnowledgeController = Depends(get_controller),
):
    result = await controller.index_meeting(meeting_id, force=force)
    return success_payload(result, f"Meeting index {result['status'].lower()}.")


@router.delete(
    "/meetings/{meeting_id}",
    summary="Remove one meeting from the search index",
    description=(
        "Deletes only the vectors belonging to this meeting, found by their "
        "`{meeting_id}#` id prefix. The meeting itself and every Supabase record "
        "stay untouched."
    ),
    responses={503: {"model": ErrorResponse, "description": "Search is not configured"}},
)
async def delete_meeting_index(
    meeting_id: str = Path(..., description="Meeting whose vectors should be removed."),
    controller: KnowledgeController = Depends(get_controller),
):
    result = await controller.delete_meeting(meeting_id)
    return success_payload(
        result, f"Removed {result['vectors_removed']} vector(s) for this meeting."
    )
