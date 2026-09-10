"""Global exception handling.

Every failure leaves the API in the same envelope:

    {"success": false, "error": {"code": "...", "message": "..."}}

Stack traces and driver messages go to the server log only. Clients receive a
stable code they can branch on and a sentence a person can act on.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_logger, get_settings
from app.utils.errors import AppError
from app.utils.responses import json_error

logger = get_logger(__name__)

# Maps bare HTTP status codes to our error vocabulary.
_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_FILE_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def register_exception_handlers(app: FastAPI) -> None:
    settings = get_settings()

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log = logger.error if exc.status_code >= 500 else logger.warning
        log(
            "%s %s -> %s %s: %s%s",
            request.method, request.url.path, exc.status_code, exc.code, exc.message,
            f" | internal: {exc.internal}" if exc.internal else "",
        )
        return json_error(exc.status_code, exc.code, exc.message, exc.details, request_id)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error.get("loc", []) if part != "body")
            fields.append({"field": location or "body", "issue": error.get("msg", "invalid value")})

        logger.warning("Validation failed on %s: %s", request.url.path, fields)
        return json_error(
            422,
            "VALIDATION_ERROR",
            "Some values in the request are not valid.",
            {"fields": fields[:20]},
            getattr(request.state, "request_id", None),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
        return json_error(
            exc.status_code, code, message,
            request_id=getattr(request.state, "request_id", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        message = "Something went wrong on the server."
        if settings.debug:
            # Detail is shown in development only, never in production.
            message = f"{message} ({type(exc).__name__}: {exc})"
        return json_error(500, "INTERNAL_ERROR", message, request_id=request_id)
