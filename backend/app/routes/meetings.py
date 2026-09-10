"""Meeting endpoints: upload, transcribe, read, list, export."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import Response

from app.controllers.meeting_controller import MeetingController
from app.models.enums import ProcessingStatus
from app.schemas.common import ErrorResponse
from app.utils.responses import success_payload

router = APIRouter(prefix="/meetings", tags=["Meetings"])

COMMON_ERRORS = {
    404: {"model": ErrorResponse, "description": "Meeting not found"},
    503: {"model": ErrorResponse, "description": "Database or external service unavailable"},
}


def get_controller() -> MeetingController:
    return MeetingController()


@router.post(
    "/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a meeting recording",
    description=(
        "Accepts an audio or video recording, validates it (extension, size, content type "
        "and a real decodable audio stream) and creates the meeting record. "
        "Transcription is a separate call so the UI can show each stage.\n\n"
        "**Rejections:** 415 unsupported format, 413 too large, 422 empty or corrupted."
    ),
    responses={
        413: {"model": ErrorResponse, "description": "File exceeds MAX_UPLOAD_SIZE_MB"},
        415: {"model": ErrorResponse, "description": "Unsupported file format"},
        422: {"model": ErrorResponse, "description": "Empty or corrupted media"},
        503: {"model": ErrorResponse, "description": "FFmpeg or database unavailable"},
    },
)
async def upload_meeting(
    file: UploadFile = File(..., description="Audio or video recording."),
    title: Optional[str] = Form(None, description="Optional title; defaults to the filename."),
    controller: MeetingController = Depends(get_controller),
):
    result = await controller.upload(file, title)
    return success_payload(result.model_dump(mode="json"), result.message)


@router.post(
    "/{meeting_id}/transcribe",
    summary="Transcribe a meeting with FFmpeg + Whisper",
    description=(
        "Runs the Milestone 1 pipeline: validate media, extract 16 kHz mono audio with "
        "FFmpeg, transcribe with Whisper, validate the transcript, then store the text and "
        "its timed segments.\n\n"
        "This call is synchronous and can take a while: expect roughly 0.3x to 1x of the "
        "recording length on CPU, depending on WHISPER_MODEL."
    ),
    responses={**COMMON_ERRORS, 409: {"model": ErrorResponse, "description": "Already processing"}},
)
async def transcribe_meeting(
    meeting_id: str = Path(..., description="Meeting id returned by the upload call."),
    force: bool = Query(False, description="Re-transcribe even if a transcript already exists."),
    controller: MeetingController = Depends(get_controller),
):
    result = await controller.transcribe(meeting_id, force=force)
    return success_payload(result.model_dump(mode="json"), "Transcript generated and saved.")


@router.get(
    "",
    summary="List meetings",
    description="Newest first. Supports paging and filtering by processing status.",
    responses={503: COMMON_ERRORS[503]},
)
async def list_meetings(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    meeting_status: Optional[ProcessingStatus] = Query(
        None, alias="status", description="Filter by processing status."
    ),
    controller: MeetingController = Depends(get_controller),
):
    result = await controller.list(limit=limit, offset=offset, status=meeting_status)
    return success_payload(result.model_dump(mode="json"), "Meetings retrieved.")


@router.get(
    "/dashboard",
    summary="Dashboard statistics",
    description="Counts for the dashboard: meetings by state, action items, participants.",
    responses={503: COMMON_ERRORS[503]},
)
async def dashboard(controller: MeetingController = Depends(get_controller)):
    result = await controller.dashboard()
    return success_payload(result.model_dump(mode="json"), "Dashboard statistics retrieved.")


@router.get(
    "/{meeting_id}",
    summary="Get one meeting",
    responses=COMMON_ERRORS,
)
async def get_meeting(
    meeting_id: str, controller: MeetingController = Depends(get_controller)
):
    result = await controller.get(meeting_id)
    return success_payload(result.model_dump(mode="json"), "Meeting retrieved.")


@router.get(
    "/{meeting_id}/transcript",
    summary="Get a transcript",
    description="Returns the paragraph text and the timed segments used by the timeline view.",
    responses=COMMON_ERRORS,
)
async def get_transcript(
    meeting_id: str, controller: MeetingController = Depends(get_controller)
):
    transcript = await controller.get_transcript(meeting_id)
    return success_payload(transcript.model_dump(mode="json"), "Transcript retrieved.")


@router.get(
    "/{meeting_id}/transcript/download",
    summary="Download a transcript",
    description=(
        "Downloads the real stored transcript in one of four formats:\n\n"
        "* `txt` - paragraph view\n"
        "* `timeline` - timestamped segments\n"
        "* `json` - full structured data\n"
        "* `csv` - one row per segment"
    ),
    response_class=Response,
    responses=COMMON_ERRORS,
)
async def download_transcript(
    meeting_id: str,
    export_format: str = Query("txt", alias="format", pattern="^(txt|timeline|json|csv)$"),
    controller: MeetingController = Depends(get_controller),
):
    content, filename, media_type = await controller.export(meeting_id, export_format)  # type: ignore[arg-type]
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.patch(
    "/{meeting_id}",
    summary="Rename a meeting",
    responses=COMMON_ERRORS,
)
async def rename_meeting(
    meeting_id: str,
    title: str = Query(..., min_length=1, max_length=255),
    controller: MeetingController = Depends(get_controller),
):
    result = await controller.rename(meeting_id, title)
    return success_payload(result.model_dump(mode="json"), "Meeting renamed.")


@router.delete(
    "/{meeting_id}",
    summary="Delete a meeting",
    description="Removes the meeting and, by cascade, its transcript, intelligence and files.",
    responses=COMMON_ERRORS,
)
async def delete_meeting(
    meeting_id: str, controller: MeetingController = Depends(get_controller)
):
    await controller.delete(meeting_id)
    return success_payload({"meeting_id": meeting_id}, "Meeting deleted.")
