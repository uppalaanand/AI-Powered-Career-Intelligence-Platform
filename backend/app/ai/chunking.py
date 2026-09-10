"""Transcript chunking for long meetings.

Why: a two-hour meeting can run to 20,000 words. Sending it in one request risks
hitting the context window and, more practically, produces a vague summary
because the important details get diluted.

Strategy - sentence-aware sliding window:

* target size   LLM_CHUNK_CHAR_SIZE   (default 9,000 characters ~= 2,200 tokens)
* overlap       LLM_CHUNK_OVERLAP_CHARS (default 600 characters)
* boundaries    sentence ends, never mid-sentence
* ceiling       LLM_MAX_CHUNKS chunks; beyond that the transcript is truncated
                and the caller is told, rather than silently spending 100 calls

The overlap matters: an action item assigned at the end of one chunk often has
its deadline stated at the start of the next. Repeating a little context keeps
those pairs together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import get_logger, get_settings
from app.utils.text import split_sentences

logger = get_logger(__name__)


@dataclass
class TranscriptChunk:
    index: int
    text: str
    char_count: int


@dataclass
class ChunkPlan:
    chunks: List[TranscriptChunk]
    was_chunked: bool
    was_truncated: bool
    total_chars: int

    @property
    def count(self) -> int:
        return len(self.chunks)


def plan_chunks(
    transcript: str,
    *,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    max_chunks: Optional[int] = None,
) -> ChunkPlan:
    settings = get_settings()
    chunk_size = chunk_size or settings.llm_chunk_char_size
    overlap = overlap if overlap is not None else settings.llm_chunk_overlap_chars
    max_chunks = max_chunks or settings.llm_max_chunks

    text = (transcript or "").strip()
    if not text:
        return ChunkPlan(chunks=[], was_chunked=False, was_truncated=False, total_chars=0)

    if len(text) <= chunk_size:
        return ChunkPlan(
            chunks=[TranscriptChunk(0, text, len(text))],
            was_chunked=False,
            was_truncated=False,
            total_chars=len(text),
        )

    overlap = max(0, min(overlap, chunk_size // 3))
    sentences = split_sentences(text) or [text]

    chunks: List[TranscriptChunk] = []
    current: List[str] = []
    current_length = 0
    truncated = False

    for sentence in sentences:
        # A single sentence longer than the window (rare, but possible with poor
        # punctuation) is hard-split so it cannot stall the loop.
        if len(sentence) > chunk_size:
            if current:
                chunks.append(_make_chunk(len(chunks), current))
                current, current_length = [], 0
            for start in range(0, len(sentence), chunk_size):
                chunks.append(_make_chunk(len(chunks), [sentence[start : start + chunk_size]]))
                if len(chunks) >= max_chunks:
                    truncated = True
                    break
            if truncated:
                break
            continue

        if current_length + len(sentence) + 1 > chunk_size and current:
            chunks.append(_make_chunk(len(chunks), current))
            if len(chunks) >= max_chunks:
                truncated = True
                break
            carry = _tail(current, overlap)
            current = list(carry)
            current_length = sum(len(part) + 1 for part in carry)

        current.append(sentence)
        current_length += len(sentence) + 1

    if current and not truncated:
        chunks.append(_make_chunk(len(chunks), current))

    if len(chunks) > max_chunks:
        chunks = chunks[:max_chunks]
        truncated = True

    if truncated:
        logger.warning(
            "Transcript exceeded LLM_MAX_CHUNKS (%s); analysing the first %s chunks only.",
            max_chunks, len(chunks),
        )

    logger.info("Split %s characters into %s chunk(s)", len(text), len(chunks))
    return ChunkPlan(
        chunks=chunks, was_chunked=True, was_truncated=truncated, total_chars=len(text)
    )


def _make_chunk(index: int, sentences: List[str]) -> TranscriptChunk:
    text = " ".join(sentences).strip()
    return TranscriptChunk(index=index, text=text, char_count=len(text))


def _tail(sentences: List[str], overlap: int) -> List[str]:
    """Last whole sentences that fit inside the overlap budget."""
    if overlap <= 0:
        return []
    carried: List[str] = []
    length = 0
    for sentence in reversed(sentences):
        if length + len(sentence) > overlap:
            break
        carried.insert(0, sentence)
        length += len(sentence) + 1
    return carried


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for logging and limits."""
    return max(1, len(text or "") // 4)
