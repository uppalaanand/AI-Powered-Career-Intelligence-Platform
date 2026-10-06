"""Integration routes."""

from fastapi import APIRouter, Depends, Body, Path

from app.controllers.integration_controller import IntegrationController
from app.schemas.common import ErrorResponse
from app.utils.responses import success_payload

router = APIRouter(prefix="/integrations", tags=["Integrations"])

COMMON_ERRORS = {
    503: {"model": ErrorResponse, "description": "Integration not configured"},
}

def get_controller() -> IntegrationController:
    return IntegrationController()

@router.post(
    "/zoom/auth",
    summary="Initiate Zoom Auth",
    responses=COMMON_ERRORS,
)
async def auth_zoom(controller: IntegrationController = Depends(get_controller)):
    await controller.auth_zoom()
    return success_payload(None, "Authenticated")

@router.get(
    "/zoom/recordings",
    summary="List Zoom recordings",
    responses=COMMON_ERRORS,
)
async def list_zoom_recordings(controller: IntegrationController = Depends(get_controller)):
    result = await controller.list_zoom_recordings()
    return success_payload(result.model_dump(mode="json"), "Zoom recordings retrieved.")

@router.post(
    "/zoom/recordings/{recording_id}/import",
    summary="Import Zoom recording",
    responses=COMMON_ERRORS,
)
async def import_zoom_recording(
    recording_id: str = Path(...),
    download_url: str = Body(..., embed=True),
    topic: str = Body("Zoom Meeting", embed=True),
    file_ext: str = Body("mp4", embed=True),
    controller: IntegrationController = Depends(get_controller)
):
    result = await controller.import_zoom_recording(recording_id, download_url, topic, file_ext)
    return success_payload(result.model_dump(mode="json"), "Zoom recording imported.")

@router.post(
    "/google-meet/auth",
    summary="Initiate Google Meet Auth",
    responses=COMMON_ERRORS,
)
async def auth_google_meet(controller: IntegrationController = Depends(get_controller)):
    await controller.auth_google_meet()
    return success_payload(None, "Authenticated")

@router.get(
    "/google-meet/recordings",
    summary="List Google Meet recordings",
    responses=COMMON_ERRORS,
)
async def list_google_meet_recordings(controller: IntegrationController = Depends(get_controller)):
    result = await controller.list_google_meet_recordings()
    return success_payload(result.model_dump(mode="json"), "Google Meet recordings retrieved.")

@router.post(
    "/google-meet/recordings/{recording_id}/import",
    summary="Import Google Meet recording",
    responses=COMMON_ERRORS,
)
async def import_google_meet_recording(
    recording_id: str = Path(...),
    download_url: str = Body(..., embed=True),
    topic: str = Body("Google Meet Recording", embed=True),
    file_ext: str = Body("mp4", embed=True),
    controller: IntegrationController = Depends(get_controller)
):
    result = await controller.import_google_meet_recording(recording_id, download_url, topic, file_ext)
    return success_payload(result.model_dump(mode="json"), "Google Meet recording imported.")
