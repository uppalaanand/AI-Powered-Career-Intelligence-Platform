"""Text normalisation helpers shared by accuracy scoring and participant mapping."""

from __future__ import annotations

import re
import unicodedata
from typing import List

_PUNCTUATION = re.compile(r"[^\w\s'-]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_NAME_TITLES = {
    "mr", "mrs", "ms", "miss", "dr", "prof", "professor", "sir", "madam",
    "sr", "jr", "eng", "er",
}

# Filler tokens Whisper often emits; ignored when comparing against a
# reference transcript because they carry no meaning.
FILLER_WORDS = {"uh", "um", "erm", "mm", "hmm", "uh-huh", "mhm", "ah", "eh"}


def normalize_for_comparison(text: str, *, drop_fillers: bool = True) -> List[str]:
    """Lower-case, strip punctuation and split into comparable word tokens.

    Used by the Word Error Rate calculation so that "Ravi," and "ravi" are not
    counted as a substitution.
    """
    if not text:
        return []
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", " ")
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    tokens = [t for t in text.split(" ") if t]
    if drop_fillers:
        tokens = [t for t in tokens if t not in FILLER_WORDS]
    return tokens


def normalize_person_name(raw: str) -> str:
    """Return a comparison key for a participant name.

    ``"  Dr. RAVI  Kumar "`` -> ``"ravi kumar"``.  Returns ``""`` when the input
    holds no usable name so callers can fall back to an "Unknown speaker" label.
    """
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw)).strip().lower()
    text = text.replace(".", " ").replace("_", " ")
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    parts = [p for p in text.split(" ") if p and p not in _NAME_TITLES]
    return " ".join(parts)


def title_case_name(raw: str) -> str:
    """Display form of a name: ``"ravi kumar"`` -> ``"Ravi Kumar"``."""
    cleaned = _WHITESPACE.sub(" ", str(raw or "").strip())
    if not cleaned:
        return ""
    return " ".join(
        part if (part.isupper() and len(part) <= 3) else part.capitalize()
        for part in cleaned.split(" ")
    )


def collapse_whitespace(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def split_sentences(text: str) -> List[str]:
    """Cheap sentence splitter used for transcript chunking.

    A dependency-free heuristic is enough here: chunk boundaries only need to be
    *reasonable*, not linguistically perfect.
    """
    if not text:
        return []
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in pieces if p and p.strip()]


def truncate(text: str, limit: int, suffix: str = "...") -> str:
    text = text or ""
    return text if len(text) <= limit else text[: max(0, limit - len(suffix))] + suffix
