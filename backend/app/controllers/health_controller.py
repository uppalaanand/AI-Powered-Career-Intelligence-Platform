"""Health and configuration reporting.

Used by the frontend to warn about missing configuration *before* the user
uploads a 200 MB video and hits a wall.
"""

from __future__ import annotations

from typing import Any, Dict

from starlette.concurrency import run_in_threadpool

from app.ai.llm_service import LLMService
from app.repositories.vector_repository import VectorRepository
from app.services.embedding_service import EmbeddingService
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
                "Groq is not configured, so meeting analysis and Q&A are unavailable. "
                "Set GROQ_API_KEY in backend/.env."
            )
        if not settings.knowledge_configured:
            missing = []
            if not settings.embeddings_configured:
                missing.append("the embedding model (pip install -r requirements.txt)")
            if not settings.vector_db_configured:
                missing.append("PINECONE_API_KEY")
            if missing:
                warnings.append(
                    "Meeting search and Q&A are unavailable. Set "
                    + " and ".join(missing)
                    + " in backend/.env. Everything else works without them."
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
            groq_configured=settings.groq_configured,
            llm_configured=settings.llm_configured,
            llm_model=settings.groq_model,
            supabase_configured=settings.supabase_configured,
            database_reachable=database_reachable,
            embeddings_configured=settings.embeddings_configured,
            vector_store_configured=settings.vector_db_configured,
            knowledge_search_ready=settings.knowledge_configured,
            warnings=warnings,
        )

    def supported_formats(self) -> SupportedFormatsResponse:
        return SupportedFormatsResponse(
            audio=AUDIO_EXTENSIONS,
            video=VIDEO_EXTENSIONS,
            accept_attribute=accept_attribute(),
            max_upload_size_mb=self._settings.max_upload_size_mb,
        )

    async def check_llm(self) -> Dict[str, Any]:
        return await LLMService().check_connection()

    async def check_vector_store(self) -> Dict[str, Any]:
        """Milestone 3 connectivity: the embedding model and the vector index.

        Operator-triggered only. The embedding probe costs one tiny request;
        the Pinecone probe is a stats call that costs no quota at all.
        """
        embeddings = await EmbeddingService().check_connection()
        vectors = await VectorRepository().check_connection()
        ready = bool(embeddings.get("reachable")) and bool(vectors.get("reachable"))
        return {
            "ready": ready,
            "embeddings": embeddings,
            "vector_store": vectors,
            "message": (
                "Meeting search is ready."
                if ready
                else "Meeting search is not ready yet. See the details below."
            ),
        }

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
