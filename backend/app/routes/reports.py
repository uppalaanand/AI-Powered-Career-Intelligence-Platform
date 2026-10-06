"""Report routes."""

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import Response

from app.controllers.report_controller import ReportController
from app.schemas.common import ErrorResponse
from app.utils.responses import success_payload

router = APIRouter(prefix="/meetings", tags=["Reports"])

COMMON_ERRORS = {
    404: {"model": ErrorResponse, "description": "Meeting not found"},
    503: {"model": ErrorResponse, "description": "Service unavailable"},
}

def get_controller() -> ReportController:
    return ReportController()

@router.get(
    "/{meeting_id}/report",
    summary="Download a meeting report",
    response_class=Response,
    responses=COMMON_ERRORS,
)
async def download_report(
    meeting_id: str = Path(...),
    export_format: str = Query("pdf", alias="format", pattern="^(pdf|csv)$"),
    controller: ReportController = Depends(get_controller),
):
    content = await controller.get_report(meeting_id, export_format)  # type: ignore[arg-type]
    
    media_type = "application/pdf" if export_format == "pdf" else "text/csv; charset=utf-8"
    ext = "pdf" if export_format == "pdf" else "csv"
    
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="report_{meeting_id}.{ext}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )

@router.get(
    "/{meeting_id}/analytics",
    summary="Get meeting analytics",
    responses=COMMON_ERRORS,
)
async def get_analytics(
    meeting_id: str = Path(...),
    controller: ReportController = Depends(get_controller),
):
    analytics = await controller.get_analytics(meeting_id)
    return success_payload(analytics.model_dump(mode="json"), "Analytics retrieved.")
