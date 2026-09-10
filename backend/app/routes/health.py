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
        "Reports whether FFmpeg, the AI providers and Supabase are configured, and lists any warnings "
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
    "/health/llm",
    summary="Check the AI provider chain",
    description=(
        "Reports the configured fallback chain (Grok -> Gemini -> Groq) and makes one "
        "tiny live call to the **primary configured provider** to prove its key and "
        "model work. The others are reported from configuration only.\n\n"
        "Pass `all=true` to live-check every configured provider - that costs one "
        "request per provider, so it is off by default on free tiers."
    ),
)
async def llm_health(
    all: bool = Query(
        False, description="Live-check every configured provider, not just the primary."
    ),
    controller: HealthController = Depends(get_controller),
):
    return success_payload(await controller.check_llm(check_all=all), "LLM check complete.")
