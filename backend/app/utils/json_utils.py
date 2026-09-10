"""Tolerant JSON extraction for LLM output.

Language models occasionally wrap JSON in prose or markdown fences even when
told not to. These helpers recover the object without ever *guessing* content:
if nothing parses, the caller retries or fails loudly.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def extract_json_object(raw: str) -> Optional[Any]:
    """Return the first JSON value found in ``raw``, or ``None``."""
    if not raw or not raw.strip():
        return None

    candidates = [raw.strip()]

    fenced = _FENCE.findall(raw)
    candidates.extend(block.strip() for block in fenced)

    balanced = _first_balanced_span(raw)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:
            continue
        for attempt in (candidate, _repair(candidate)):
            try:
                return json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _first_balanced_span(text: str) -> Optional[str]:
    """Slice out the outermost {...} or [...] block, ignoring braces in strings."""
    start_index = None
    opener = closer = ""
    for index, char in enumerate(text):
        if char in "{[":
            start_index = index
            opener = char
            closer = "}" if char == "{" else "]"
            break
    if start_index is None:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return None


def _repair(text: str) -> str:
    """Fix the two malformations models actually produce: trailing commas and
    smart quotes. Deliberately conservative - no content is invented."""
    repaired = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
