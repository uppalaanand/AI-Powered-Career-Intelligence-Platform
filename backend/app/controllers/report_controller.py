"""Report controller."""

from typing import Optional, Union
from starlette.concurrency import run_in_threadpool

from app.services.report_service import ReportService, ExportFormat
from app.schemas.report import AnalyticsResponse
from app.utils.errors import BadRequestError

class ReportController:
    def __init__(self, service: Optional[ReportService] = None) -> None:
        self._service = service or ReportService()

    async def get_report(self, meeting_id: str, export_format: ExportFormat) -> Union[bytes, str]:
        if export_format not in ("pdf", "csv"):
            raise BadRequestError(f"Unsupported format: {export_format}")
            
        return await run_in_threadpool(
            self._service.generate_report, meeting_id, export_format
        )

    async def get_analytics(self, meeting_id: str) -> AnalyticsResponse:
        return await run_in_threadpool(
            self._service.get_analytics, meeting_id
        )
