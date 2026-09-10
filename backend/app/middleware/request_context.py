"""Request id + access logging middleware.

Every request gets an id that appears in the logs and in error responses, so a
user-reported failure can be found in the server log without guesswork.
"""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_logger, get_settings

logger = get_logger("app.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        header = settings.request_id_header
        request_id = request.headers.get(header) or uuid.uuid4().hex[:12]
        request.state.request_id = request_id

        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers[header] = request_id
        if request.url.path not in ("/api/health", "/health"):
            logger.info(
                "%s %s -> %s in %.0fms [%s]",
                request.method, request.url.path, response.status_code, elapsed_ms, request_id,
            )
        return response
