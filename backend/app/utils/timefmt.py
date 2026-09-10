"""Timestamp formatting for transcript segments."""

from __future__ import annotations

from datetime import datetime, timezone


def format_timestamp(seconds: float, *, always_hours: bool = False) -> str:
    """``73.4`` -> ``"01:13"``; ``3675`` -> ``"01:01:15"``."""
    try:
        total = max(0, int(round(float(seconds))))
    except (TypeError, ValueError):
        total = 0
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours or always_hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_range(start: float, end: float) -> str:
    return f"{format_timestamp(start)} - {format_timestamp(end)}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return "0s"
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
