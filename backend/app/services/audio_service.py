"""FFmpeg layer: inspect media and normalise it for Whisper.

Pipeline implemented here:

    video/audio upload -> ffprobe (is it real media?) -> ffmpeg
    -> 16 kHz mono PCM WAV -> Whisper

Whisper resamples internally to 16 kHz mono anyway, so doing it once up front
with FFmpeg is both faster and removes every container/codec difference between
an MKV screen recording and an M4A phone memo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import get_logger, get_settings
from app.schemas.meeting import MediaMetadata
from app.utils.errors import AudioProcessingError, CorruptedMediaError, FFmpegNotAvailableError
from app.utils.files import which

logger = get_logger(__name__)


class AudioService:
    """Thin, well-guarded wrapper around the ffmpeg/ffprobe binaries."""

    def __init__(self) -> None:
        self._settings = get_settings()

    # ------------------------------------------------------------- binaries
    def ffmpeg_binary(self) -> str:
        binary = which(self._settings.ffmpeg_path)
        if not binary:
            raise FFmpegNotAvailableError(
                "FFmpeg was not found. Install it and make sure `ffmpeg -version` works, "
                "or set FFMPEG_PATH in backend/.env."
            )
        return binary

    def ffprobe_binary(self) -> str:
        binary = which(self._settings.ffprobe_path)
        if not binary:
            raise FFmpegNotAvailableError(
                "FFprobe was not found. It ships with FFmpeg. Set FFPROBE_PATH in backend/.env "
                "if it is installed somewhere unusual."
            )
        return binary

    def is_available(self) -> bool:
        try:
            self.ffmpeg_binary()
            self.ffprobe_binary()
            return True
        except FFmpegNotAvailableError:
            return False

    def version(self) -> Optional[str]:
        try:
            result = self._run([self.ffmpeg_binary(), "-version"], timeout=15)
            return result.stdout.splitlines()[0] if result.stdout else None
        except Exception:  # noqa: BLE001 - version reporting is best effort
            return None

    # ---------------------------------------------------------------- probe
    def probe(self, path: Path) -> MediaMetadata:
        """Read real container/stream information. Raises for undecodable files."""
        command = [
            self.ffprobe_binary(),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        result = self._run(command, timeout=120)

        if result.returncode != 0:
            logger.warning("ffprobe rejected %s: %s", path.name, result.stderr.strip()[:400])
            raise CorruptedMediaError(
                "This file could not be read as audio or video. It may be corrupted "
                "or only partially uploaded.",
                internal=result.stderr.strip()[:1000],
            )

        try:
            payload: Dict[str, Any] = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise CorruptedMediaError(internal=str(exc)) from exc

        streams = payload.get("streams") or []
        container = payload.get("format") or {}
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        video = next((s for s in streams if s.get("codec_type") == "video"), None)

        duration = _to_float(container.get("duration")) or _to_float(
            (audio or {}).get("duration")
        )

        return MediaMetadata(
            duration_seconds=duration,
            container=container.get("format_name"),
            has_audio_stream=audio is not None,
            has_video_stream=video is not None,
            audio_codec=(audio or {}).get("codec_name"),
            video_codec=(video or {}).get("codec_name"),
            sample_rate=_to_int((audio or {}).get("sample_rate")),
            channels=_to_int((audio or {}).get("channels")),
            bit_rate=_to_int(container.get("bit_rate")),
        )

    # -------------------------------------------------------------- convert
    def extract_audio(self, source: Path, destination: Path) -> Path:
        """Extract, downmix and resample to the WAV layout Whisper expects."""
        settings = self._settings
        destination.parent.mkdir(parents=True, exist_ok=True)

        command = [
            self.ffmpeg_binary(),
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-y",
            "-i", str(source),
            "-vn",                                   # drop any video stream
            "-map", "0:a:0",                         # first audio stream only
            "-ac", str(settings.audio_channels),     # mono
            "-ar", str(settings.audio_sample_rate),  # 16 kHz
            "-acodec", "pcm_s16le",                  # uncompressed 16-bit PCM
            str(destination),
        ]

        logger.info("Extracting audio: %s -> %s", source.name, destination.name)
        result = self._run(command, timeout=settings.ffmpeg_timeout_seconds)

        if result.returncode != 0:
            detail = result.stderr.strip()[:400]
            logger.error("FFmpeg conversion failed for %s: %s", source.name, detail)
            if "does not contain any stream" in detail.lower() or "stream map" in detail.lower():
                raise CorruptedMediaError(
                    "This recording has no audio track, so there is nothing to transcribe.",
                    internal=detail,
                )
            raise AudioProcessingError(internal=detail)

        if not destination.exists() or destination.stat().st_size == 0:
            raise AudioProcessingError(
                "Audio extraction produced an empty file.",
                internal=result.stderr.strip()[:400],
            )
        return destination

    # ------------------------------------------------------------- internal
    @staticmethod
    def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(  # noqa: S603 - command list is built from config, never user input
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise FFmpegNotAvailableError(internal=str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioProcessingError(
                f"Media processing timed out after {timeout} seconds. "
                "Try a shorter recording or raise FFMPEG_TIMEOUT_SECONDS.",
                internal=str(exc),
            ) from exc


def _to_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
