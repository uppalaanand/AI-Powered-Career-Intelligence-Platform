"""RAG question-answering schemas (Milestone 3).

``RAGAnswer`` is the contract the LLM must satisfy, validated exactly the way
``LLMMeetingIntelligence`` is in Milestone 2: a malformed answer is rejected
rather than returned, so a confused model cannot crash or mislead the API.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.search import MAX_QUERY_CHARS, SearchFilters
from app.utils.text import collapse_whitespace


class AskRequest(BaseModel):
    question: str = Field(
        ...,
        description="Natural-language question, e.g. 'what deadline was decided for the mobile app?'",
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=50, description="Passages to retrieve as context. Defaults to RAG_TOP_K."
    )
    filters: Optional[SearchFilters] = None

    @field_validator("question", mode="before")
    @classmethod
    def _clean_question(cls, value: Any) -> Any:
        text = collapse_whitespace(str(value or ""))
        if not text:
            raise ValueError("Enter a question about your meetings.")
        return text[:MAX_QUERY_CHARS]


class SourceReference(BaseModel):
    """A meeting the answer was built from, so the answer can be checked."""

    meeting_id: str
    meeting_title: str
    meeting_date: Optional[str] = None
    source_type: Optional[str] = None
    excerpt: Optional[str] = Field(None, description="The passage the model was shown.")
    score: Optional[float] = None


class RAGAnswer(BaseModel):
    """Exactly the JSON shape Groq is asked to return.

    ``extra="ignore"`` keeps a chatty model from injecting stray keys, and the
    validators below mean the API layer never has to defend itself against a
    missing field.
    """

    model_config = ConfigDict(extra="ignore")

    answer: str = Field(..., description="The grounded answer.")
    answer_found: bool = Field(
        True, description="False when the retrieved context did not contain the answer."
    )
    used_meeting_ids: List[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"

    @field_validator("answer", mode="before")
    @classmethod
    def _clean_answer(cls, value: Any) -> Any:
        text = collapse_whitespace(str(value or ""))
        if not text:
            raise ValueError("The model returned an empty answer.")
        return text

    @field_validator("answer_found", mode="before")
    @classmethod
    def _coerce_found(cls, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if value is None:
            return True
        text = str(value).strip().lower()
        if text in {"false", "no", "0", "none", "null"}:
            return False
        if text in {"true", "yes", "1"}:
            return True
        return True

    @field_validator("used_meeting_ids", mode="before")
    @classmethod
    def _clean_ids(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        cleaned: List[str] = []
        for item in value:
            # Models sometimes return [{"meeting_id": "..."}] instead of ["..."].
            if isinstance(item, dict):
                item = item.get("meeting_id") or item.get("id") or ""
            text = collapse_whitespace(str(item or ""))
            if text and text not in cleaned:
                cleaned.append(text)
        return cleaned

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> Any:
        if value is None:
            return "medium"
        text = str(value).strip().lower()
        aliases = {"certain": "high", "very high": "high", "strong": "high",
                   "moderate": "medium", "normal": "medium", "unsure": "low",
                   "weak": "low", "very low": "low"}
        return aliases.get(text, text if text in {"high", "medium", "low"} else "medium")


class AskResponse(BaseModel):
    """What the frontend renders: the answer plus where it came from."""

    question: str
    answer: str
    answer_found: bool = True
    confidence: str = "medium"
    sources: List[SourceReference] = Field(default_factory=list)
    provider: Optional[str] = Field(None, description="LLM provider that answered (groq).")
    model: Optional[str] = None
    searched_meetings: int = 0
    took_ms: int = 0
    message: Optional[str] = None
