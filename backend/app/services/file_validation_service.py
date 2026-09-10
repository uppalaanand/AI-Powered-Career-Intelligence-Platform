"""Upload validation.

Four independent gates, cheapest first, so a bad file is rejected before any
expensive work happens:

1. filename + extension   - is this a format we claim to support?
2. size                   - not empty, not larger than MAX_UPLOAD_SIZE_MB
3. content sniff          - do the file's magic bytes agree with the extension?
4. ffprobe                - is there really a decodable audio stream inside?

Step 3 is the reason validation does not trust the extension alone: renaming
`report.pdf` to `report.mp3` fails the sniff, and a truncated MP4 fails ffprobe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import get_logger, get_settings
from app.models.enums import MediaKind
from app.models.media_formats import GENERIC_MIME_TYPES, MediaFormat, lookup
from app.schemas.meeting import MediaMetadata
from app.services.audio_service import AudioService
from app.utils.errors import (
    CorruptedMediaError,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from app.utils.files import get_extension, sanitize_filename

logger = get_logger(__name__)

# Magic-byte signatures. Used when python-magic/libmagic is unavailable (common
# on Windows), so MIME checking degrades gracefully instead of being skipped.
_SIGNATURES: list[tuple[bytes, int, str]] = [
    (b"ID3", 0, "audio/mpeg"),
    (b"\xff\xfb", 0, "audio/mpeg"),
    (b"\xff\xf3", 0, "audio/mpeg"),
    (b"\xff\xf2", 0, "audio/mpeg"),
    (b"RIFF", 0, "audio/wav"),
    (b"fLaC", 0, "audio/flac"),
    (b"OggS", 0, "audio/ogg"),
    (b"ftyp", 4, "video/mp4"),
    (b"\x1aE\xdf\xa3", 0, "video/x-matroska"),
    (b"\x30\x26\xb2\x75", 0, "video/x-ms-asf"),
    (b"\x00\x00\x01\xba", 0, "video/mpeg"),
    (b"\x00\x00\x01\xb3", 0, "video/mpeg"),
]

# Content types that are definitely not media, listed so the error message can
# be specific instead of a generic "unsupported".
_OBVIOUS_NON_MEDIA = {
    b"%PDF": "PDF document",
    b"PK\x03\x04": "ZIP archive or Office document",
    b"\x89PNG": "PNG image",
    b"\xff\xd8\xff": "JPEG image",
    b"GIF8": "GIF image",
    b"{\n": "JSON or text file",
}


@dataclass
class ValidatedFile:
    """Everything downstream code needs to know about an accepted upload."""

    path: Path
    safe_filename: str
    original_filename: str
    extension: str
    media_format: MediaFormat
    media_kind: MediaKind
    mime_type: Optional[str]
    size_bytes: int
    metadata: MediaMetadata


class FileValidationService:
    def __init__(self, audio_service: Optional[AudioService] = None) -> None:
        self._settings = get_settings()
        self._audio = audio_service or AudioService()

    # ------------------------------------------------------------ gate 1+2
    def validate_upload_metadata(self, filename: Optional[str], size_bytes: int) -> MediaFormat:
        """Check name, extension and size before the body is written to disk."""
        safe_name = sanitize_filename(filename)
        extension = get_extension(safe_name)

        if not extension:
            raise UnsupportedFileTypeError(
                "The file has no extension, so its format cannot be determined."
            )

        media_format = lookup(extension)
        if media_format is None:
            raise UnsupportedFileTypeError(
                f"'.{extension}' files are not supported. Upload an audio or video "
                "recording such as MP3, WAV, M4A, MP4 or MOV.",
                details={"extension": extension},
            )

        if size_bytes <= 0:
            raise EmptyFileError()
        if size_bytes < self._settings.min_upload_size_bytes:
            raise EmptyFileError(
                "The uploaded file is too small to contain a recording.",
                details={"size_bytes": size_bytes},
            )
        if size_bytes > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"The file is {size_bytes / (1024 * 1024):.1f} MB. "
                f"The limit is {self._settings.max_upload_size_mb} MB.",
                details={
                    "size_bytes": size_bytes,
                    "max_size_bytes": self._settings.max_upload_size_bytes,
                },
            )
        return media_format

    # -------------------------------------------------------------- gate 3
    def sniff_mime_type(self, path: Path) -> Optional[str]:
        """Best-effort content type from the file's own bytes."""
        try:
            import magic  # type: ignore

            return magic.from_file(str(path), mime=True)
        except Exception:  # noqa: BLE001 - libmagic is optional
            return self._sniff_signature(path)

    @staticmethod
    def _sniff_signature(path: Path) -> Optional[str]:
        try:
            with path.open("rb") as handle:
                header = handle.read(64)
        except OSError:
            return None

        for signature, offset, mime in _SIGNATURES:
            if header[offset : offset + len(signature)] == signature:
                return mime
        return None

    def check_content_type(self, path: Path, media_format: MediaFormat) -> Optional[str]:
        header = self._read_header(path)
        for signature, description in _OBVIOUS_NON_MEDIA.items():
            if header.startswith(signature):
                raise UnsupportedFileTypeError(
                    f"This looks like a {description}, not an audio or video recording.",
                    details={"detected": description},
                )

        detected = self.sniff_mime_type(path)
        if detected and detected not in GENERIC_MIME_TYPES:
            family = detected.split("/")[0]
            if detected not in media_format.mime_types and family not in {"audio", "video"}:
                raise UnsupportedFileTypeError(
                    f"The file contents ({detected}) do not match a '.{media_format.extension}' "
                    "recording. The file may have been renamed.",
                    details={"detected_mime_type": detected,
                             "expected": sorted(media_format.mime_types)},
                )
        return detected

    @staticmethod
    def _read_header(path: Path, size: int = 64) -> bytes:
        try:
            with path.open("rb") as handle:
                return handle.read(size)
        except OSError:
            return b""

    # -------------------------------------------------------------- gate 4
    def validate_media_streams(self, path: Path) -> MediaMetadata:
        metadata = self._audio.probe(path)

        if not metadata.has_audio_stream:
            raise CorruptedMediaError(
                "This recording has no audio track, so there is nothing to transcribe.",
                details={"container": metadata.container},
            )
        if metadata.duration_seconds is not None and metadata.duration_seconds < 0.5:
            raise CorruptedMediaError(
                "The recording is shorter than half a second.",
                details={"duration_seconds": metadata.duration_seconds},
            )
        return metadata

    # ----------------------------------------------------------- full pass
    def validate(self, path: Path, original_filename: str) -> ValidatedFile:
        """Run gates 1-4 against a file already written to the temp workspace."""
        safe_name = sanitize_filename(original_filename)
        size_bytes = path.stat().st_size
        media_format = self.validate_upload_metadata(safe_name, size_bytes)
        mime_type = self.check_content_type(path, media_format)
        metadata = self.validate_media_streams(path)

        logger.info(
            "Accepted upload %s (%s, %.1f MB, %.1fs)",
            safe_name,
            media_format.label,
            size_bytes / (1024 * 1024),
            metadata.duration_seconds or 0,
        )

        return ValidatedFile(
            path=path,
            safe_filename=safe_name,
            original_filename=original_filename,
            extension=media_format.extension,
            media_format=media_format,
            media_kind=media_format.kind,
            mime_type=mime_type,
            size_bytes=size_bytes,
            metadata=metadata,
        )
