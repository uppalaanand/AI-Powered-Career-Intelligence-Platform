"""Meeting knowledge documents - the bridge between Supabase rows and vectors.

A *knowledge document* is one self-contained, embeddable passage of a meeting
plus the metadata needed to trace it home:

    Supabase rows  ->  KnowledgeDocument[]  ->  embeddings  ->  Pinecone

Nothing here talks to a network. This module is pure: rows in, documents out,
which is what makes Task 1 straightforward to test without any credentials.

What gets embedded, and why
---------------------------
=================  ==========================================================
transcript         The meeting's own words, split into small overlapping
                   passages. This is what answers "when was X discussed?".
summary            The condensed version of the whole meeting.
decision           One document per decision - short, high-signal text that
                   retrieval should be able to hit exactly.
action_item        One per task, carrying owner, deadline, priority and status
                   in both the text and the metadata.
key_point          One per key point.
participants       One roster document per meeting, so "who attended the
                   launch meeting?" retrieves something.
=================  ==========================================================

Deadlines deliberately get **no document type of their own**: every deadline
already lives inside its action item, in the text ("Deadline: Friday") and in
the metadata (``deadline``/``has_deadline``). A separate deadline vector would
be a near-duplicate of the action item and would spend embedding quota twice
for the same sentence.

Every document carries a deterministic id::

    {meeting_id}#{source_type}#{source_id}#{chunk_index}

so re-indexing a meeting overwrites its vectors instead of duplicating them,
and every vector belonging to a meeting shares the ``{meeting_id}#`` prefix -
which is how deletion finds them again.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.ai.chunking import plan_chunks
from app.config import get_logger, get_settings
from app.utils.text import collapse_whitespace, truncate

logger = get_logger(__name__)

#: Source types, in the order they are built. Also the allowed values for the
#: ``source_type`` metadata filter exposed by the search API.
SOURCE_TYPES = (
    "summary",
    "decision",
    "action_item",
    "key_point",
    "participants",
    "transcript",
)

#: Separator inside a vector id. Chosen because it cannot appear in a UUID, and
#: because Pinecone prefix listing treats the id as an opaque string.
ID_SEPARATOR = "#"

#: Pinecone rejects metadata values over ~40 KB per vector; keep the stored
#: snippet well under that while still being useful to show in the UI.
MAX_METADATA_CONTENT_CHARS = 1500

#: Passages shorter than this carry no retrievable meaning ("ok", "yes").
MIN_EMBEDDABLE_CHARS = 12


@dataclass
class KnowledgeDocument:
    """One embeddable passage of a meeting."""

    meeting_id: str
    source_type: str
    source_id: str
    chunk_index: int
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def vector_id(self) -> str:
        """Deterministic id: re-indexing updates in place, never duplicates."""
        return ID_SEPARATOR.join(
            [self.meeting_id, self.source_type, self.source_id, str(self.chunk_index)]
        )

    def to_metadata(self) -> Dict[str, Any]:
        """Metadata stored alongside the vector.

        Always includes the full trace - meeting, source type, source row and
        chunk - so a match can be resolved back to the exact record it came
        from. ``content`` is stored (truncated) so search results can show an
        excerpt without a second database round-trip.
        """
        payload: Dict[str, Any] = {
            "meeting_id": self.meeting_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "chunk_index": self.chunk_index,
            "content": truncate(self.content, MAX_METADATA_CONTENT_CHARS),
        }
        # Pinecone metadata accepts strings, numbers, booleans and string lists
        # only; anything else is dropped rather than risking a rejected upsert.
        # Empty values are dropped too: storing deadline="" would make a filter
        # for "has a deadline" match items that have none.
        for key, value in self.metadata.items():
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value
            elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
                payload[key] = list(value)
        return payload


def build_documents(bundle: Dict[str, Any]) -> List[KnowledgeDocument]:
    """Turn one meeting's Supabase records into embeddable documents.

    ``bundle`` is what ``KnowledgeRepository.get_meeting_bundle`` returns: the
    meeting row plus its intelligence rows. Missing pieces are simply skipped -
    a meeting with a transcript but no analysis still indexes its transcript.
    """
    meeting = bundle.get("meeting") or {}
    meeting_id = str(meeting.get("id") or "").strip()
    if not meeting_id:
        return []

    shared = _shared_metadata(meeting)
    documents: List[KnowledgeDocument] = []

    documents.extend(_summary_documents(meeting_id, bundle, shared))
    documents.extend(_decision_documents(meeting_id, bundle, shared))
    documents.extend(_action_item_documents(meeting_id, bundle, shared))
    documents.extend(_key_point_documents(meeting_id, bundle, shared))
    documents.extend(_participant_documents(meeting_id, bundle, shared))
    documents.extend(_transcript_documents(meeting_id, meeting, shared))

    settings = get_settings()
    ceiling = settings.knowledge_max_chunks_per_meeting
    if len(documents) > ceiling:
        logger.warning(
            "Meeting %s produced %s knowledge documents; indexing the first %s.",
            meeting_id, len(documents), ceiling,
        )
        documents = documents[:ceiling]

    logger.info("Built %s knowledge document(s) for meeting %s", len(documents), meeting_id)
    return documents


def fingerprint(documents: List[KnowledgeDocument], signature: str = "") -> str:
    """Stable identity of everything that would be embedded, *and by which model*.

    Returned as ``"{signature}:{sha256}"``, where ``signature`` is the embedding
    model's ``model@dimensions``. Re-indexing compares this with the stored
    value: identical means the existing vectors are still correct, so nothing is
    re-embedded. Because the model is part of the value, switching the
    embedding model changes every fingerprint and forces a clean re-embed -
    otherwise unchanged meetings would be skipped and keep vectors from the old
    model, which cannot be compared with the new model's query vectors.
    """
    digest = hashlib.sha256()
    digest.update(signature.encode("utf-8"))
    digest.update(b"\x00")
    for document in documents:
        digest.update(document.vector_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(document.content.encode("utf-8"))
        digest.update(b"\x00")
    return f"{signature}:{digest.hexdigest()}" if signature else digest.hexdigest()


# --------------------------------------------------------------- builders
def _shared_metadata(meeting: Dict[str, Any]) -> Dict[str, Any]:
    """Meeting-level metadata copied onto every vector for filtering/display."""
    created_at = meeting.get("created_at")
    return {
        "meeting_title": collapse_whitespace(str(meeting.get("title") or "Untitled meeting")),
        "meeting_date": str(created_at) if created_at else None,
        # Numeric form so Pinecone range filters ($gte/$lte) work on dates.
        "meeting_date_ts": _date_sort_key(created_at),
    }


def _summary_documents(
    meeting_id: str, bundle: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    summary = _clean(bundle.get("summary"))
    if not summary:
        return []
    title = shared.get("meeting_title") or ""
    body = f"Summary of the meeting '{title}': {summary}"
    return _chunked_documents(
        meeting_id, "summary", "meeting-summary", body, shared
    )


def _decision_documents(
    meeting_id: str, bundle: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    documents: List[KnowledgeDocument] = []
    for position, row in enumerate(bundle.get("decisions") or []):
        text = _clean(row.get("text"))
        if not text:
            continue
        context = _clean(row.get("context"))
        body = f"Decision made in the meeting: {text}"
        if context:
            body += f" Context: {context}"
        documents.append(
            KnowledgeDocument(
                meeting_id=meeting_id,
                source_type="decision",
                source_id=_row_id(row, "decision", position),
                chunk_index=0,
                content=body,
                metadata={**shared, "position": position},
            )
        )
    return documents


def _action_item_documents(
    meeting_id: str, bundle: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    """One document per task. Owner, deadline, priority and status are written
    into the text *and* the metadata, so they are both searchable and filterable
    without a second vector type for deadlines."""
    documents: List[KnowledgeDocument] = []
    for position, row in enumerate(bundle.get("action_items") or []):
        task = _clean(row.get("task"))
        if not task:
            continue
        owner = _clean(row.get("assigned_to"))
        deadline = _clean(row.get("deadline"))
        priority = _clean(row.get("priority")) or "medium"
        status = _clean(row.get("status")) or "pending"
        context = _clean(row.get("context"))

        parts = [f"Action item from the meeting: {task}"]
        parts.append(f"Assigned to: {owner}." if owner else "Assigned to: not stated.")
        parts.append(f"Deadline: {deadline}." if deadline else "Deadline: not stated.")
        parts.append(f"Priority: {priority}. Status: {status}.")
        if context:
            parts.append(f"Context: {context}")

        documents.append(
            KnowledgeDocument(
                meeting_id=meeting_id,
                source_type="action_item",
                source_id=_row_id(row, "action-item", position),
                chunk_index=0,
                content=" ".join(parts),
                metadata={
                    **shared,
                    "position": position,
                    "assigned_to": owner,
                    "deadline": deadline,
                    "has_deadline": bool(deadline),
                    "priority": priority,
                    "status": status,
                },
            )
        )
    return documents


def _key_point_documents(
    meeting_id: str, bundle: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    documents: List[KnowledgeDocument] = []
    for position, row in enumerate(bundle.get("key_points") or []):
        text = _clean(row.get("text"))
        if not text:
            continue
        documents.append(
            KnowledgeDocument(
                meeting_id=meeting_id,
                source_type="key_point",
                source_id=_row_id(row, "key-point", position),
                chunk_index=0,
                content=f"Key point discussed in the meeting: {text}",
                metadata={**shared, "position": position},
            )
        )
    return documents


def _participant_documents(
    meeting_id: str, bundle: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    """One roster document per meeting rather than one per person.

    "Who was in the launch meeting?" wants the whole list back in a single
    match, and one vector for a roster costs far less quota than one per name.
    """
    names: List[str] = []
    for row in bundle.get("participants") or []:
        name = _clean(row.get("name"))
        role = _clean(row.get("role"))
        if not name:
            continue
        names.append(f"{name} ({role})" if role else name)

    if not names:
        return []

    title = shared.get("meeting_title") or ""
    body = f"Participants in the meeting '{title}': {', '.join(names)}."
    return [
        KnowledgeDocument(
            meeting_id=meeting_id,
            source_type="participants",
            source_id="meeting-participants",
            chunk_index=0,
            content=body,
            metadata={**shared, "participants": names[:32]},
        )
    ]


def _transcript_documents(
    meeting_id: str, meeting: Dict[str, Any], shared: Dict[str, Any]
) -> List[KnowledgeDocument]:
    """Transcript split with the project's existing chunker.

    ``plan_chunks`` already does sentence-aware splitting with overlap; passing
    the smaller knowledge window reuses that logic instead of writing a second,
    subtly different splitter.
    """
    transcript = _clean(meeting.get("transcript_text"))
    if not transcript:
        return []

    settings = get_settings()
    plan = plan_chunks(
        transcript,
        chunk_size=settings.knowledge_chunk_char_size,
        overlap=settings.knowledge_chunk_overlap_chars,
        max_chunks=settings.knowledge_max_chunks_per_meeting,
    )

    documents: List[KnowledgeDocument] = []
    for chunk in plan.chunks:
        text = _clean(chunk.text)
        if len(text) < MIN_EMBEDDABLE_CHARS:
            continue
        documents.append(
            KnowledgeDocument(
                meeting_id=meeting_id,
                source_type="transcript",
                source_id="transcript",
                chunk_index=chunk.index,
                content=text,
                metadata={**shared, "position": chunk.index},
            )
        )
    return documents


# ---------------------------------------------------------------- helpers
def _chunked_documents(
    meeting_id: str,
    source_type: str,
    source_id: str,
    text: str,
    shared: Dict[str, Any],
) -> List[KnowledgeDocument]:
    """Split a long single-source passage (a summary) if it exceeds the window."""
    settings = get_settings()
    if len(text) <= settings.knowledge_chunk_char_size:
        return [
            KnowledgeDocument(
                meeting_id=meeting_id,
                source_type=source_type,
                source_id=source_id,
                chunk_index=0,
                content=text,
                metadata=dict(shared),
            )
        ]

    plan = plan_chunks(
        text,
        chunk_size=settings.knowledge_chunk_char_size,
        overlap=settings.knowledge_chunk_overlap_chars,
        max_chunks=settings.knowledge_max_chunks_per_meeting,
    )
    return [
        KnowledgeDocument(
            meeting_id=meeting_id,
            source_type=source_type,
            source_id=source_id,
            chunk_index=chunk.index,
            content=_clean(chunk.text),
            metadata=dict(shared),
        )
        for chunk in plan.chunks
        if len(_clean(chunk.text)) >= MIN_EMBEDDABLE_CHARS
    ]


def _clean(value: Any) -> str:
    """Normalise text for embedding only. The stored record is never modified."""
    if value is None:
        return ""
    return collapse_whitespace(str(value))


def _row_id(row: Dict[str, Any], prefix: str, position: int) -> str:
    """Prefer the database row id; fall back to position for unsaved rows."""
    row_id = row.get("id")
    if row_id:
        return str(row_id)
    return f"{prefix}-{position}"


def _date_sort_key(created_at: Any) -> Optional[float]:
    """`created_at` as a POSIX timestamp, for Pinecone numeric range filters."""
    if not created_at:
        return None
    from datetime import datetime

    if isinstance(created_at, datetime):
        return created_at.timestamp()
    try:
        return datetime.fromisoformat(str(created_at).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
