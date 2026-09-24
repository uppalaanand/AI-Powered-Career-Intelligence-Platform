"""Health, configuration and connectivity checks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.controllers.health_controller import HealthController
from app.utils.responses import success_payload

router = APIRouter(tags=["System"])


def get_controller() -> HealthController:
    return HealthController()


@router.get(
    "/health",
    summary="Service health and configuration",
    description=(
        "Reports whether FFmpeg, Groq, meeting search and Supabase are configured, and lists any warnings "
        "the operator should fix. Pass `deep=true` to also ping the database."
    ),
)
async def health(
    deep: bool = Query(False, description="Also check database connectivity."),
    controller: HealthController = Depends(get_controller),
):
    result = await controller.health(deep=deep)
    return success_payload(result.model_dump(mode="json"), "Health check complete.")


@router.get(
    "/config/formats",
    summary="Supported upload formats and limits",
    description="Drives the upload screen's format list, accept attribute and size limit.",
)
async def formats(controller: HealthController = Depends(get_controller)):
    return success_payload(
        controller.supported_formats().model_dump(mode="json"), "Supported formats retrieved."
    )


@router.get(
    "/health/ffmpeg",
    summary="Check the FFmpeg installation",
)
async def ffmpeg_health(controller: HealthController = Depends(get_controller)):
    return success_payload(await controller.ffmpeg_info(), "FFmpeg check complete.")


@router.get(
    "/health/database",
    summary="Check the Supabase connection",
    description="Confirms the credentials work and the `meetings` table exists.",
)
async def database_health(controller: HealthController = Depends(get_controller)):
    return success_payload(await controller.check_database(), "Database check complete.")


@router.get(
    "/health/vector",
    summary="Check the meeting search stack (Milestone 3)",
    description=(
        "Verifies the two services semantic search depends on: the embedding "
        "model and the Pinecone index.\n\n"
        "Reports the index dimension against the configured embedding size, which "
        "is the mistake that otherwise only shows up as a rejected upsert."
    ),
)
async def vector_health(controller: HealthController = Depends(get_controller)):
    return success_payload(await controller.check_vector_store(), "Search check complete.")


@router.get(
    "/health/llm",
    summary="Check the Groq connection",
    description=(
        "Makes one tiny live call to Groq - the application's only LLM provider - to "
        "prove GROQ_API_KEY and GROQ_MODEL work. Operator-triggered only: nothing in "
        "the analysis or search path calls this."
    ),
)
async def llm_health(controller: HealthController = Depends(get_controller)):
    return success_payload(await controller.check_llm(), "LLM check complete.")
