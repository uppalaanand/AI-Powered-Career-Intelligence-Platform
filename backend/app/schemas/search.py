"""Semantic search schemas (Milestone 3).

Follows the existing schema style: validators normalise input at the edge so no
service downstream has to re-check it, and every response field is something the
React UI actually renders.
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.knowledge.documents import SOURCE_TYPES
from app.utils.text import collapse_whitespace

MAX_QUERY_CHARS = 1000


class SearchFilters(BaseModel):
    """Optional metadata filters applied inside the vector search itself.

    Filtering in Pinecone rather than afterwards matters: post-filtering would
    throw away matches that were already paid for and could return fewer
    results than asked for.
    """

    meeting_id: Optional[str] = Field(
        None, description="Restrict the search to a single meeting."
    )
    source_type: Optional[str] = Field(
        None, description=f"One of: {', '.join(SOURCE_TYPES)}."
    )
    date_from: Optional[str] = Field(
        None, description="ISO date. Only meetings created on or after this date."
    )
    date_to: Optional[str] = Field(
        None, description="ISO date. Only meetings created on or before this date."
    )
    participant: Optional[str] = Field(
        None, description="Only action items assigned to this person."
    )

    @field_validator("meeting_id", "source_type", "participant", mode="before")
    @classmethod
    def _clean(cls, value: Any) -> Any:
        if value is None:
            return None
        text = collapse_whitespace(str(value))
        return text or None

    @field_validator("source_type")
    @classmethod
    def _known_source_type(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of: {', '.join(SOURCE_TYPES)}")
        return value

    @property
    def is_empty(self) -> bool:
        return not any(
            [self.meeting_id, self.source_type, self.date_from, self.date_to, self.participant]
        )


class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        description="Natural-language search, e.g. 'which meeting discussed the database migration?'",
    )
    top_k: Optional[int] = Field(
        None, ge=1, le=50, description="How many passages to retrieve. Defaults to SEARCH_DEFAULT_TOP_K."
    )
    filters: Optional[SearchFilters] = None

    @field_validator("query", mode="before")
    @classmethod
    def _clean_query(cls, value: Any) -> Any:
        text = collapse_whitespace(str(value or ""))
        if not text:
            raise ValueError("Enter a question or some search terms.")
        return text[:MAX_QUERY_CHARS]


class SearchMatch(BaseModel):
    """One retrieved passage, traced back to its source record."""

    meeting_id: str
    source_type: str
    source_id: Optional[str] = None
    chunk_index: int = 0
    content: str = Field(..., description="The passage that matched.")
    score: float = Field(..., description="Similarity, 0-1. Higher is closer.")


class SearchResult(BaseModel):
    """One meeting, with the passages that made it relevant.

    Results are grouped by meeting rather than returned as a flat list of
    chunks: five passages from one meeting is one answer to "which meeting
    discussed X", not five.
    """

    model_config = ConfigDict(populate_by_name=True)

    meeting_id: str
    meeting_title: str
    meeting_date: Optional[str] = None
    status: Optional[str] = None
    score: float = Field(..., description="Best passage score for this meeting.")
    matched_source_types: List[str] = Field(default_factory=list)
    excerpt: str = Field("", description="Best-matching passage, for display.")
    matches: List[SearchMatch] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult] = Field(default_factory=list)
    total_matches: int = Field(0, description="Passages returned by the vector search.")
    took_ms: int = Field(0, description="Server-side time for the whole search.")
    message: Optional[str] = Field(
        None, description="Set when there is something the user should know, e.g. no results."
    )
