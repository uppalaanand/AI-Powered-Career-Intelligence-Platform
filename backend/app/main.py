"""FastAPI application entrypoint.

Run locally:

    uvicorn app.main:app --reload --port 8000

Interactive API documentation: http://localhost:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import configure_logging, get_logger, get_settings
from app.middleware import RequestContextMiddleware, register_exception_handlers
from app.routes import api_router
from app.services.audio_service import AudioService
from app.utils.files import purge_stale_workspaces

configure_logging()
logger = get_logger(__name__)

DESCRIPTION = """
Backend for the **AI Career Intelligence Platform**.

Two milestones, one pipeline:

**Milestone 1 - audio processing and transcription**
Upload a recording, validate it, extract normalised audio with FFmpeg, transcribe
it with Whisper, validate the transcript, store it, and measure accuracy against a
reference transcript using Word Error Rate.

**Milestone 2 - LLM processing**
Send the transcript to the AI provider chain - Grok (xAI) first, then Google Gemini,
then Groq, stopping at the first valid answer - validate the structured JSON it returns
against a strict schema, map participants, extract action items with deadlines and
priorities, and persist everything to Supabase.

Every response uses the same envelope:

```json
{ "success": true, "data": { }, "message": "Operation completed successfully" }
```

```json
{ "success": false, "error": { "code": "ERROR_CODE", "message": "Human-readable error" } }
```
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting %s v%s (%s)", settings.app_name, settings.app_version, settings.app_env)

    purge_stale_workspaces()

    if not AudioService().is_available():
        logger.warning(
            "FFmpeg was not found. Transcription will fail until it is installed "
            "or FFMPEG_PATH is set in backend/.env."
        )
    if not settings.supabase_configured:
        logger.warning(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY "
            "in backend/.env and run backend/database/schema.sql."
        )
    if settings.llm_configured:
        logger.info(
            "AI provider chain: %s", " -> ".join(settings.configured_llm_providers)
        )
    else:
        logger.warning(
            "No AI provider is configured. Set XAI_API_KEY (Grok), GEMINI_API_KEY "
            "or GROQ_API_KEY in backend/.env. Transcription still works."
        )

    logger.info(
        "Whisper: %s / model '%s' on %s | CORS: %s",
        settings.whisper_backend, settings.whisper_model, settings.whisper_device,
        ", ".join(settings.cors_origin_list),
    )
    yield
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "AI Career Intelligence Platform"},
    )

    # CORS is driven by configuration: localhost in development, the deployed
    # frontend domain in production. Never "*" with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", settings.request_id_header],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/", tags=["System"], summary="Service banner")
    async def root() -> dict:
        return {
            "success": True,
            "data": {
                "name": settings.app_name,
                "version": settings.app_version,
                "docs": "/docs",
                "health": "/api/health",
            },
            "message": "AI Career Intelligence Platform API is running.",
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - convenience runner
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
