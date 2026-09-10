"""Temporary media storage.

Design decision (documented in docs/PIPELINE_AND_FEATURES.md):

Upload and transcription are two separate API calls, so the uploaded recording
has to survive between them. It is written once to a per-meeting directory under
the configured temp root and deleted as soon as transcription finishes. Nothing
is ever streamed fully into memory - files are copied in 1 MB blocks, so a
200 MB video costs 1 MB of RAM.

    <temp_root>/staging-<random>/    while the upload is being validated
    <temp_root>/meeting-<id>/        after the meeting row exists
    (deleted once the transcript is stored, unless KEEP_TEMP_FILES=true)

Only the transcript is durable. The media file is never persisted, which keeps
storage costs and privacy exposure low.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from app.config import get_logger, get_settings
from app.utils.errors import FileTooLargeError
from app.utils.files import ensure_inside, sanitize_filename

logger = get_logger(__name__)

COPY_CHUNK_SIZE = 1024 * 1024  # 1 MB


class MediaStore:
    def __init__(self) -> None:
        self._settings = get_settings()

    # ------------------------------------------------------------- staging
    def create_staging_dir(self) -> Path:
        path = self._settings.temp_root / f"staging-{uuid.uuid4().hex}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_stream(self, source: BinaryIO, directory: Path, filename: str) -> Path:
        """Copy an upload to disk in blocks, enforcing the size limit as we go."""
        safe_name = sanitize_filename(filename)
        destination = ensure_inside(directory, directory / safe_name)
        limit = self._settings.max_upload_size_bytes
        written = 0

        with destination.open("wb") as handle:
            while True:
                block = source.read(COPY_CHUNK_SIZE)
                if not block:
                    break
                written += len(block)
                if written > limit:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    raise FileTooLargeError(
                        f"The file is larger than the {self._settings.max_upload_size_mb} MB limit.",
                        details={"max_size_bytes": limit},
                    )
                handle.write(block)

        logger.info("Wrote upload %s (%.1f MB)", safe_name, written / (1024 * 1024))
        return destination

    # ------------------------------------------------------------- meeting
    def meeting_dir(self, meeting_id: str) -> Path:
        path = self._settings.temp_root / f"meeting-{meeting_id}"
        return ensure_inside(self._settings.temp_root, path)

    def promote(self, staging_file: Path, meeting_id: str) -> Path:
        """Move a validated upload from staging into its meeting directory."""
        target_dir = self.meeting_dir(meeting_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = target_dir / staging_file.name
        shutil.move(str(staging_file), str(destination))
        shutil.rmtree(staging_file.parent, ignore_errors=True)
        return destination

    def find_media(self, meeting_id: str) -> Optional[Path]:
        directory = self.meeting_dir(meeting_id)
        if not directory.exists():
            return None
        for entry in sorted(directory.iterdir()):
            if entry.is_file() and not entry.name.startswith("audio-"):
                return entry
        return None

    def working_audio_path(self, meeting_id: str) -> Path:
        directory = self.meeting_dir(meeting_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"audio-{meeting_id}.wav"

    def cleanup(self, meeting_id: str) -> None:
        if self._settings.keep_temp_files:
            logger.info("KEEP_TEMP_FILES=true, keeping media for meeting %s", meeting_id)
            return
        shutil.rmtree(self.meeting_dir(meeting_id), ignore_errors=True)
        logger.info("Removed temporary media for meeting %s", meeting_id)

    def discard_staging(self, directory: Path) -> None:
        shutil.rmtree(directory, ignore_errors=True)
