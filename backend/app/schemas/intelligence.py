"""Meeting intelligence schemas.

`MeetingIntelligence` is the single source of truth for what the LLM is allowed
to return. Anything that fails these validators is rejected and retried rather
than stored, so malformed AI output never reaches the database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import UNKNOWN_PARTICIPANT, ActionItemStatus, Priority
from app.utils.text import collapse_whitespace

# Strings the model uses to say "not stated in the transcript".
_NULL_TOKENS = {
    "", "null", "none", "n/a", "na", "not specified", "not mentioned",
    "unspecified", "unknown", "tbd", "to be decided", "-", "--",
}


def _nullable_str(value: Any) -> Optional[str]:
    """Collapse the many ways a model says "nothing here" into ``None``."""
    if value is None:
        return None
    text = collapse_whitespace(str(value))
    return None if text.lower() in _NULL_TOKENS else text


class ActionItem(BaseModel):
    """A task somebody committed to during the meeting."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    task: str = Field(..., min_length=1, description="What has to be done.")
    assigned_to: Optional[str] = Field(
        None, description="Participant responsible. Null when the transcript never says."
    )
    deadline: Optional[str] = Field(
        None, description="Deadline exactly as spoken, e.g. 'Friday'. Null when not stated."
    )
    priority: Priority = Field(
        Priority.MEDIUM, description="high | medium | low. Defaults to medium when not implied."
    )
    status: ActionItemStatus = Field(ActionItemStatus.PENDING)
    context: Optional[str] = Field(None, description="Short transcript evidence for this item.")

    @field_validator("task", mode="before")
    @classmethod
    def _clean_task(cls, value: Any) -> Any:
        return collapse_whitespace(str(value or ""))

    @field_validator("assigned_to", "deadline", "context", mode="before")
    @classmethod
    def _clean_optional(cls, value: Any) -> Any:
        return _nullable_str(value)

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, value: Any) -> Any:
        if value is None:
            return Priority.MEDIUM
        if isinstance(value, Priority):
            return value  # already validated; str() would mangle it
        text = str(value).strip().lower()
        aliases = {
            "urgent": "high", "critical": "high", "p0": "high", "p1": "high", "highest": "high",
            "normal": "medium", "moderate": "medium", "med": "medium", "p2": "medium",
            "minor": "low", "lowest": "low", "p3": "low", "nice to have": "low",
        }
        return aliases.get(text, text)

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        if value is None:
            return ActionItemStatus.PENDING
        if isinstance(value, ActionItemStatus):
            return value  # already validated; str() would mangle it
        text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "open": "pending", "todo": "pending", "to_do": "pending", "not_started": "pending",
            "ongoing": "in_progress", "started": "in_progress", "wip": "in_progress",
            "doing": "in_progress", "done": "completed", "closed": "completed",
            "finished": "completed", "stuck": "blocked", "on_hold": "blocked",
        }
        return aliases.get(text, text)


class Participant(BaseModel):
    """A person mentioned in the meeting, after normalisation and de-duplication."""

    id: Optional[str] = None
    name: str = Field(..., min_length=1, description="Display name, e.g. 'Ravi Kumar'.")
    normalized_name: Optional[str] = Field(None, description="Comparison key, e.g. 'ravi kumar'.")
    role: Optional[str] = Field(None, description="Role or team, only when stated.")
    is_unknown: bool = Field(False, description="True for placeholder speakers.")
    mention_count: int = Field(1, ge=0)
    aliases: List[str] = Field(default_factory=list, description="Other spellings merged into this record.")

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, value: Any) -> Any:
        cleaned = collapse_whitespace(str(value or ""))
        return cleaned or UNKNOWN_PARTICIPANT

    @field_validator("role", mode="before")
    @classmethod
    def _clean_role(cls, value: Any) -> Any:
        return _nullable_str(value)


class Decision(BaseModel):
    id: Optional[str] = None
    text: str = Field(..., min_length=1)
    context: Optional[str] = None

    @field_validator("text", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        return collapse_whitespace(str(value or ""))


class KeyPoint(BaseModel):
    id: Optional[str] = None
    text: str = Field(..., min_length=1)

    @field_validator("text", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        return collapse_whitespace(str(value or ""))


class LLMMeetingIntelligence(BaseModel):
    """Exactly the JSON shape every provider is asked to return.

    Grok, Gemini and Groq all validate against this one model, so nothing
    downstream can tell which of them answered.

    ``extra="ignore"`` keeps an over-eager model from injecting stray keys, while
    every required field is validated. Lists default to empty so an honest "there
    were no decisions" answer is representable without inventing content.
    """

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(..., description="Neutral prose summary of the meeting.")
    key_points: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    participants: List[str] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def _clean_summary(cls, value: Any) -> Any:
        return collapse_whitespace(str(value or ""))

    @field_validator("key_points", "decisions", "participants", mode="before")
    @classmethod
    def _clean_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        cleaned: List[str] = []
        for item in value:
            # Models sometimes return [{"text": "..."}] instead of ["..."].
            if isinstance(item, dict):
                item = item.get("text") or item.get("point") or item.get("name") or ""
            text = collapse_whitespace(str(item or ""))
            if text and text.lower() not in _NULL_TOKENS:
                cleaned.append(text)
        return cleaned

    @field_validator("action_items", mode="before")
    @classmethod
    def _drop_empty_items(cls, value: Any) -> Any:
        if not value:
            return []
        if isinstance(value, dict):
            value = [value]
        return [item for item in value if item and (not isinstance(item, dict) or item.get("task"))]


class MeetingIntelligence(BaseModel):
    """Persisted, fully-mapped intelligence returned to the frontend."""

    meeting_id: str
    summary: str
    key_points: List[KeyPoint] = Field(default_factory=list)
    decisions: List[Decision] = Field(default_factory=list)
    participants: List[Participant] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    model: Optional[str] = Field(None, description="Model that produced this result.")
    provider: Optional[str] = Field(
        None, description="AI provider that produced this result: grok | gemini | groq."
    )
    chunk_count: int = 1
    generated_at: Optional[datetime] = None

    @property
    def deadlines(self) -> List[Dict[str, Any]]:
        """Flat deadline view for the UI - derived, never invented."""
        return [
            {"task": item.task, "assigned_to": item.assigned_to,
             "deadline": item.deadline, "priority": item.priority.value}
            for item in self.action_items
            if item.deadline
        ]


class AnalyzeRequest(BaseModel):
    force: bool = Field(
        False,
        description=(
            "Re-run the analysis and replace existing results for this meeting. "
            "Without it an already-analysed meeting is served from the database "
            "and no AI provider is called."
        ),
    )


class IntelligenceResponse(BaseModel):
    meeting_id: str
    intelligence: MeetingIntelligence
