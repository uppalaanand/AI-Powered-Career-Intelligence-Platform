"""Health and configuration reporting.

Used by the frontend to warn about missing configuration *before* the user
uploads a 200 MB video and hits a wall.
"""

from __future__ import annotations

from typing import Any, Dict

from starlette.concurrency import run_in_threadpool

from app.ai.llm_orchestrator import LLMOrchestrator
from app.config import get_settings
from app.models.media_formats import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    accept_attribute,
)
from app.repositories import supabase_client
from app.schemas.common import HealthResponse
from app.schemas.meeting import SupportedFormatsResponse
from app.services.audio_service import AudioService


class HealthController:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._audio = AudioService()

    async def health(self, deep: bool = False) -> HealthResponse:
        settings = self._settings
        ffmpeg_available = await run_in_threadpool(self._audio.is_available)

        database_reachable = None
        if deep and settings.supabase_configured:
            database_reachable = await run_in_threadpool(supabase_client.ping)

        warnings: list[str] = []
        if not ffmpeg_available:
            warnings.append(
                "FFmpeg was not found. Install it, or set FFMPEG_PATH in backend/.env."
            )
        if not settings.supabase_configured:
            warnings.append(
                "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
                "in backend/.env, then run backend/database/schema.sql."
            )
        if not settings.llm_configured:
            warnings.append(
                "No AI provider is configured. Set XAI_API_KEY (Grok), GEMINI_API_KEY "
                "or GROQ_API_KEY in backend/.env."
            )
        elif not settings.grok_configured:
            # Not a failure: the chain simply starts at the first key that exists.
            warnings.append(
                "Grok is not configured, so analysis starts at "
                f"{settings.configured_llm_providers[0]}. Set XAI_API_KEY to use it first."
            )
        if database_reachable is False:
            warnings.append(
                "Supabase is configured but unreachable, or the tables are missing. "
                "Run backend/database/schema.sql in the Supabase SQL editor."
            )

        return HealthResponse(
            status="ok" if not warnings else "degraded",
            version=settings.app_version,
            environment=settings.app_env,
            ffmpeg_available=ffmpeg_available,
            whisper_backend=settings.whisper_backend,
            whisper_model=settings.whisper_model,
            grok_configured=settings.grok_configured,
            gemini_configured=settings.gemini_configured,
            groq_configured=settings.groq_configured,
            llm_configured=settings.llm_configured,
            llm_provider_chain=settings.configured_llm_providers,
            supabase_configured=settings.supabase_configured,
            database_reachable=database_reachable,
            warnings=warnings,
        )

    def supported_formats(self) -> SupportedFormatsResponse:
        return SupportedFormatsResponse(
            audio=AUDIO_EXTENSIONS,
            video=VIDEO_EXTENSIONS,
            accept_attribute=accept_attribute(),
            max_upload_size_mb=self._settings.max_upload_size_mb,
        )

    async def check_llm(self, *, check_all: bool = False) -> Dict[str, Any]:
        return await LLMOrchestrator().check_connection(check_all=check_all)

    async def check_database(self) -> Dict[str, Any]:
        settings = self._settings
        if not settings.supabase_configured:
            return {
                "configured": False,
                "reachable": False,
                "message": "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env.",
            }
        reachable = await run_in_threadpool(supabase_client.ping)
        return {
            "configured": True,
            "reachable": reachable,
            "message": (
                "Supabase responded and the meetings table exists."
                if reachable
                else "Supabase is unreachable or backend/database/schema.sql has not been run."
            ),
        }

    async def ffmpeg_info(self) -> Dict[str, Any]:
        available = await run_in_threadpool(self._audio.is_available)
        version = await run_in_threadpool(self._audio.version) if available else None
        return {
            "available": available,
            "version": version,
            "message": (
                "FFmpeg is installed and reachable."
                if available
                else "FFmpeg was not found. Install it and verify with `ffmpeg -version`."
            ),
        }
