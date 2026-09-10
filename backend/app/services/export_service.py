"""Transcript exports.

Four formats, all generated from the stored transcript - nothing here is a
placeholder file:

* ``txt``      paragraph view, ready to read or paste into a document
* ``timeline`` timestamped segments, one block per segment
* ``json``     full structured data including per-segment timings
* ``csv``      one row per segment, for spreadsheets
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Literal

from app.schemas.meeting import Transcript
from app.utils.timefmt import format_range, format_timestamp

ExportFormat = Literal["txt", "timeline", "json", "csv"]

MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "timeline": "text/plain; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
}


class ExportService:
    def render(self, transcript: Transcript, export_format: ExportFormat, title: str = "") -> str:
        if export_format == "txt":
            return self.as_paragraphs(transcript, title)
        if export_format == "timeline":
            return self.as_timeline(transcript, title)
        if export_format == "csv":
            return self.as_csv(transcript)
        return self.as_json(transcript, title)

    # ---------------------------------------------------------------- views
    def as_paragraphs(self, transcript: Transcript, title: str = "") -> str:
        return f"{self._header(transcript, title)}\n\n{transcript.text.strip()}\n"

    def as_timeline(self, transcript: Transcript, title: str = "") -> str:
        lines = [self._header(transcript, title), ""]
        if not transcript.segments:
            lines.append("(No timed segments are available for this transcript.)")
            return "\n".join(lines) + "\n"

        for segment in transcript.segments:
            speaker = f"{segment.speaker}: " if segment.speaker else ""
            lines.append(format_range(segment.start_time, segment.end_time))
            lines.append(f"{speaker}{segment.text}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def as_json(self, transcript: Transcript, title: str = "") -> str:
        payload = {
            "meeting_id": transcript.meeting_id,
            "title": title or None,
            "language": transcript.language,
            "model": transcript.model,
            "backend": transcript.backend,
            "duration_seconds": transcript.duration_seconds,
            "word_count": transcript.word_count,
            "generated_at": transcript.created_at.isoformat() if transcript.created_at else None,
            "text": transcript.text,
            "segments": [
                {
                    "segment_index": segment.segment_index,
                    "start_time": round(segment.start_time, 3),
                    "end_time": round(segment.end_time, 3),
                    "start_display": format_timestamp(segment.start_time),
                    "end_display": format_timestamp(segment.end_time),
                    "speaker": segment.speaker,
                    "text": segment.text,
                    "confidence": segment.confidence,
                }
                for segment in transcript.segments
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def as_csv(self, transcript: Transcript) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            ["segment_index", "start_seconds", "end_seconds", "start_display",
             "end_display", "speaker", "text"]
        )
        for segment in transcript.segments:
            writer.writerow([
                segment.segment_index,
                round(segment.start_time, 3),
                round(segment.end_time, 3),
                format_timestamp(segment.start_time),
                format_timestamp(segment.end_time),
                segment.speaker or "",
                segment.text,
            ])
        return buffer.getvalue()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _header(transcript: Transcript, title: str) -> str:
        parts = [f"# {title or 'Meeting transcript'}"]
        details = []
        if transcript.duration_seconds:
            details.append(f"duration {format_timestamp(transcript.duration_seconds, always_hours=True)}")
        if transcript.language:
            details.append(f"language {transcript.language}")
        if transcript.model:
            details.append(f"whisper model {transcript.model}")
        details.append(f"{transcript.word_count} words")
        parts.append("# " + ", ".join(details))
        return "\n".join(parts)

    @staticmethod
    def filename(title: str, export_format: ExportFormat) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", title or "meeting").strip("_").lower() or "meeting"
        slug = slug[:60]
        suffix = {
            "txt": "transcript.txt",
            "timeline": "transcript_timeline.txt",
            "json": "transcript.json",
            "csv": "transcript.csv",
        }[export_format]
        return f"{slug}_{suffix}"

    @staticmethod
    def media_type(export_format: ExportFormat) -> str:
        return MEDIA_TYPES.get(export_format, "text/plain; charset=utf-8")
