"""Helpers that build the single response envelope used by every endpoint."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse


def success_payload(data: Any = None, message: str = "Operation completed successfully") -> Dict[str, Any]:
    return {"success": True, "data": data, "message": message}


def error_payload(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    error: Dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"success": False, "error": error}


def json_error(
    status_code: int,
    code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    payload = error_payload(code, message, details)
    if request_id:
        payload["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=payload)
