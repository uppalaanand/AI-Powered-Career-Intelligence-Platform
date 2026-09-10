"""Generic response envelopes used by the OpenAPI documentation."""

from __future__ import annotations

from typing import Any, Dict, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["UNSUPPORTED_FILE_TYPE"])
    message: str = Field(..., examples=["The uploaded file format is not supported."])
    details: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
    request_id: Optional[str] = None


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    message: str = "Operation completed successfully"


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    ffmpeg_available: bool
    whisper_backend: str
    whisper_model: str
    grok_configured: bool
    gemini_configured: bool = False
    groq_configured: bool = False
    llm_configured: bool = False
    llm_provider_chain: list[str] = Field(
        default_factory=list,
        description="Configured AI providers in fallback order, e.g. ['grok', 'gemini'].",
    )
    supabase_configured: bool
    database_reachable: Optional[bool] = None
    warnings: list[str] = Field(default_factory=list)
