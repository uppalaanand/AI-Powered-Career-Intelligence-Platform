"""Transcription accuracy endpoints (Milestone 1 accuracy testing)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.controllers.accuracy_controller import AccuracyController
from app.schemas.accuracy import AccuracyRequest
from app.schemas.common import ErrorResponse
from app.utils.responses import success_payload

router = APIRouter(prefix="/transcription", tags=["Transcription accuracy"])


def get_controller() -> AccuracyController:
    return AccuracyController()


@router.post(
    "/accuracy",
    summary="Measure transcription accuracy against a reference transcript",
    description=(
        "Compares a generated transcript with a human reference transcript and reports "
        "Word Error Rate, accuracy (100 - WER), and the individual missing, incorrect and "
        "extra words.\n\n"
        "Pass `meeting_id` to score a stored transcript, or `hypothesis_transcript` to score "
        "arbitrary text. `target_accuracy` defaults to the 90% milestone target."
    ),
    responses={
        404: {"model": ErrorResponse, "description": "Meeting or transcript not found"},
        422: {"model": ErrorResponse, "description": "Reference transcript missing or too long"},
    },
)
async def measure_accuracy(
    payload: AccuracyRequest = Body(...),
    controller: AccuracyController = Depends(get_controller),
):
    result = await controller.compare(payload)
    return success_payload(
        result.model_dump(mode="json"),
        f"Accuracy {result.accuracy_percentage:.2f}% against a {result.target_accuracy:.0f}% target.",
    )
