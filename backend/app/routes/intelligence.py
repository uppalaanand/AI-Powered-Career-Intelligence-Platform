"""Meeting intelligence endpoints (Milestone 2)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Path

from app.controllers.intelligence_controller import IntelligenceController
from app.schemas.common import ErrorResponse
from app.schemas.intelligence import AnalyzeRequest
from app.utils.responses import success_payload

router = APIRouter(prefix="/meetings", tags=["Meeting intelligence"])


def get_controller() -> IntelligenceController:
    return IntelligenceController()


@router.post(
    "/{meeting_id}/analyze",
    summary="Analyse a transcript with Groq",
    description=(
        "Sends the stored transcript to **Groq**, the application's only LLM provider, "
        "and returns validated meeting intelligence: summary, key points, decisions, "
        "participants and action items with deadlines, priorities and statuses.\n\n"
        "A normal-length transcript costs **one** Groq request, which returns every field "
        "at once. Long transcripts are split into overlapping chunks, analysed and "
        "merged. Malformed AI output is corrected once and otherwise rejected - it is "
        "never stored.\n\n"
        "An already-analysed meeting is returned from the database untouched, without "
        "calling any provider; pass `force: true` to re-run and replace the previous result."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Meeting or transcript not found"},
        429: {"model": ErrorResponse, "description": "Groq rate limit or quota reached"},
        502: {"model": ErrorResponse, "description": "Groq returned an unusable response"},
        503: {"model": ErrorResponse, "description": "GROQ_API_KEY is not configured"},
    },
)
async def analyze_meeting(
    meeting_id: str = Path(..., description="Meeting to analyse."),
    payload: AnalyzeRequest = Body(default=AnalyzeRequest()),
    controller: IntelligenceController = Depends(get_controller),
):
    result = await controller.analyze(meeting_id, force=payload.force)
    return success_payload(result.model_dump(mode="json"), "Meeting intelligence generated.")


@router.get(
    "/{meeting_id}/intelligence",
    summary="Get stored meeting intelligence",
    responses={404: {"model": ErrorResponse, "description": "Not analysed yet"}},
)
async def get_intelligence(
    meeting_id: str, controller: IntelligenceController = Depends(get_controller)
):
    result = await controller.get(meeting_id)
    return success_payload(result.model_dump(mode="json"), "Meeting intelligence retrieved.")
