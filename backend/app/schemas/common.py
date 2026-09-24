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
    groq_configured: bool = False
    llm_configured: bool = Field(
        False, description="True when Groq, the only LLM provider, has an API key."
    )
    llm_provider: str = "groq"
    llm_model: Optional[str] = None
    supabase_configured: bool
    database_reachable: Optional[bool] = None
    embeddings_configured: bool = False
    vector_store_configured: bool = False
    knowledge_search_ready: bool = Field(
        False, description="True when semantic search and Q&A can run (Milestone 3).",
    )
    warnings: list[str] = Field(default_factory=list)
