from fastapi import APIRouter

from app.routes.health import router as health_router
from app.routes.intelligence import router as intelligence_router
from app.routes.meetings import router as meetings_router
from app.routes.transcription import router as transcription_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
api_router.include_router(meetings_router)
api_router.include_router(intelligence_router)
api_router.include_router(transcription_router)

__all__ = ["api_router"]
