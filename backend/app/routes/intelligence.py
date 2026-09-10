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
    summary="Analyse a transcript with the AI provider chain",
    description=(
        "Sends the stored transcript to the AI provider chain and returns validated "
        "meeting intelligence: summary, key points, decisions, participants and action "
        "items with deadlines, priorities and statuses.\n\n"
        "**Providers are tried in order - Grok (xAI), then Google Gemini, then Groq - "
        "one at a time, and the first valid response wins.** A provider with no API key "
        "is skipped without being called, and `intelligence.provider` in the response "
        "says which one answered.\n\n"
        "Long transcripts are split into overlapping chunks, analysed in parallel and "
        "merged. Malformed AI output is retried against the schema and never stored.\n\n"
        "An already-analysed meeting is returned from the database untouched, without "
        "calling any provider; pass `force: true` to re-run and replace the previous result."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Meeting or transcript not found"},
        429: {"model": ErrorResponse, "description": "Every configured provider is rate limited"},
        502: {"model": ErrorResponse, "description": "No provider returned a valid response"},
        503: {"model": ErrorResponse, "description": "No AI provider API key is configured"},
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
