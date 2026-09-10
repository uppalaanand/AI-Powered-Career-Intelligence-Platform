"""Meeting intelligence orchestration.

    stored transcript -> LLMOrchestrator (Grok -> Gemini -> Groq) -> validated JSON
    -> participant mapping -> Supabase -> API response

Provider choice and fallback belong to the orchestrator; this service only asks
for meeting intelligence and records which provider supplied it. The LLM never
writes to the database directly: its output is validated, then participant names
are normalised and action items are linked to those canonical names, and only
then is anything stored.

Re-analysis is opt-in. An already-analysed meeting is served from Supabase
unless the caller passes ``force``, so simply opening a meeting never spends an
API call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.ai.llm_orchestrator import LLMOrchestrator
from app.config import get_logger
from app.models.enums import ActionItemStatus, ProcessingStatus, Priority
from app.repositories.intelligence_repository import IntelligenceRepository
from app.repositories.meeting_repository import MeetingRepository
from app.schemas.intelligence import (
    ActionItem,
    Decision,
    KeyPoint,
    MeetingIntelligence,
    Participant,
)
from app.services.participant_service import ParticipantService
from app.utils.errors import AppError, IntelligenceNotFoundError, TranscriptNotFoundError
from app.utils.text import truncate

logger = get_logger(__name__)


class IntelligenceService:
    def __init__(
        self,
        llm: Optional[LLMOrchestrator] = None,
        meetings: Optional[MeetingRepository] = None,
        repository: Optional[IntelligenceRepository] = None,
        participants: Optional[ParticipantService] = None,
    ) -> None:
        self._llm = llm or LLMOrchestrator()
        self._meetings = meetings or MeetingRepository()
        self._repository = repository or IntelligenceRepository()
        self._participants = participants or ParticipantService()

    # -------------------------------------------------------------- analyse
    async def analyze(self, meeting_id: str, *, force: bool = False) -> MeetingIntelligence:
        meeting = self._meetings.get_or_404(meeting_id)
        transcript = meeting.get("transcript_text")

        if not transcript or not transcript.strip():
            raise TranscriptNotFoundError(
                "This meeting has no transcript yet. Run transcription before analysis.",
                details={"meeting_id": meeting_id},
            )

        if not force and self._repository.has_intelligence(meeting_id):
            logger.info("Returning existing intelligence for meeting %s", meeting_id)
            return self.get(meeting_id)

        try:
            self._meetings.set_status(meeting_id, ProcessingStatus.AI_ANALYSIS)
            raw, metadata = await self._llm.analyze_transcript(
                transcript, meeting_title=meeting.get("title") or ""
            )

            # Participant mapping: normalise, de-duplicate, link assignees.
            participants, action_items = self._participants.build(
                raw.participants, raw.action_items
            )

            intelligence = MeetingIntelligence(
                meeting_id=meeting_id,
                summary=raw.summary,
                key_points=[KeyPoint(text=text) for text in raw.key_points],
                decisions=[Decision(text=text) for text in raw.decisions],
                participants=participants,
                action_items=action_items,
                model=metadata.get("model"),
                provider=metadata.get("provider"),
                chunk_count=metadata.get("chunk_count", 1),
                generated_at=datetime.utcnow(),
            )

            self._meetings.set_status(meeting_id, ProcessingStatus.PERSISTING)
            self._repository.replace_intelligence(meeting_id, intelligence)
            self._meetings.set_status(meeting_id, ProcessingStatus.COMPLETED)
            return intelligence

        except AppError as exc:
            self._fail(meeting_id, exc.code, exc.message)
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected analysis failure for meeting %s", meeting_id)
            self._fail(meeting_id, "AI_ANALYSIS_FAILED",
                       "AI analysis failed unexpectedly. Check the backend logs.")
            raise

    # ------------------------------------------------------------------ get
    def get(self, meeting_id: str) -> MeetingIntelligence:
        self._meetings.get_or_404(meeting_id)
        stored = self._repository.get_intelligence(meeting_id)
        if not stored:
            raise IntelligenceNotFoundError(details={"meeting_id": meeting_id})
        return _from_rows(meeting_id, stored)

    def exists(self, meeting_id: str) -> bool:
        return self._repository.has_intelligence(meeting_id)

    async def check_llm(self, *, check_all: bool = False) -> Dict[str, Any]:
        return await self._llm.check_connection(check_all=check_all)

    def _fail(self, meeting_id: str, code: str, message: str) -> None:
        try:
            self._meetings.set_status(
                meeting_id, ProcessingStatus.FAILED,
                error_code=code, error_message=truncate(message, 500),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not record failure state for meeting %s", meeting_id)


def _from_rows(meeting_id: str, stored: Dict[str, Any]) -> MeetingIntelligence:
    action_items: List[ActionItem] = []
    for row in stored.get("action_items", []):
        action_items.append(
            ActionItem(
                id=str(row.get("id")) if row.get("id") else None,
                task=row.get("task") or "",
                assigned_to=row.get("assigned_to"),
                deadline=row.get("deadline"),
                priority=_safe_enum(Priority, row.get("priority"), Priority.MEDIUM),
                status=_safe_enum(ActionItemStatus, row.get("status"), ActionItemStatus.PENDING),
                context=row.get("context"),
            )
        )

    participants = [
        Participant(
            id=str(row.get("id")) if row.get("id") else None,
            name=row.get("name") or "Unknown",
            normalized_name=row.get("normalized_name"),
            role=row.get("role"),
            is_unknown=bool(row.get("is_unknown")),
            mention_count=row.get("mention_count") or 1,
            aliases=row.get("aliases") or [],
        )
        for row in stored.get("participants", [])
    ]

    return MeetingIntelligence(
        meeting_id=meeting_id,
        summary=stored.get("summary") or "",
        key_points=[
            KeyPoint(id=str(row.get("id")) if row.get("id") else None, text=row.get("text") or "")
            for row in stored.get("key_points", [])
            if (row.get("text") or "").strip()
        ],
        decisions=[
            Decision(
                id=str(row.get("id")) if row.get("id") else None,
                text=row.get("text") or "",
                context=row.get("context"),
            )
            for row in stored.get("decisions", [])
            if (row.get("text") or "").strip()
        ],
        participants=participants,
        action_items=action_items,
        model=stored.get("model"),
        provider=stored.get("provider"),
        chunk_count=stored.get("chunk_count") or 1,
        generated_at=_parse_datetime(stored.get("generated_at")),
    )


def _safe_enum(enum_cls, value: Any, fallback):
    try:
        return enum_cls(str(value).lower())
    except (ValueError, AttributeError):
        return fallback


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
