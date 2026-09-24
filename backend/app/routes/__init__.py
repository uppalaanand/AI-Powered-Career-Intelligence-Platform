from fastapi import APIRouter

from app.routes.health import router as health_router
from app.routes.intelligence import router as intelligence_router
from app.routes.knowledge import router as knowledge_router
from app.routes.meetings import router as meetings_router
from app.routes.rag import router as rag_router
from app.routes.search import router as search_router
from app.routes.transcription import router as transcription_router

api_router = APIRouter(prefix="/api")
api_router.include_router(health_router)
# Milestone 3 search/ask share the `/meetings` prefix with the meeting routes.
# They are registered first so the literal paths `/meetings/search` and
# `/meetings/ask` are matched before the `/meetings/{meeting_id}` wildcard.
api_router.include_router(search_router)
api_router.include_router(rag_router)
api_router.include_router(meetings_router)
api_router.include_router(intelligence_router)
api_router.include_router(transcription_router)
api_router.include_router(knowledge_router)

__all__ = ["api_router"]
