"""Speech-to-text.

Two Whisper implementations are supported behind one interface, chosen with
``WHISPER_BACKEND``:

* ``faster-whisper`` (default) - the CTranslate2 build. Same OpenAI Whisper
  weights, roughly 4x faster on CPU, no PyTorch download.
* ``openai``                   - the reference ``openai-whisper`` package.

Both return identical ``TranscriptSegment`` objects, so nothing else in the
application knows or cares which one is loaded. The model is loaded once and
cached, because loading weights takes far longer than transcribing.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Tuple

from app.config import get_logger, get_settings
from app.schemas.meeting import TranscriptSegment
from app.utils.errors import TranscriptionError
from app.utils.text import collapse_whitespace

logger = get_logger(__name__)


class TranscriptionResult:
    def __init__(
        self,
        text: str,
        segments: List[TranscriptSegment],
        language: Optional[str],
        duration: Optional[float],
        model: str,
        backend: str,
        elapsed_seconds: float,
    ) -> None:
        self.text = text
        self.segments = segments
        self.language = language
        self.duration = duration
        self.model = model
        self.backend = backend
        self.elapsed_seconds = elapsed_seconds


class WhisperBackend(ABC):
    """Interface every speech-to-text backend implements."""

    name: str = "abstract"

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def transcribe(self, audio_path: Path) -> Tuple[List[TranscriptSegment], Optional[str], Optional[float]]: ...


class FasterWhisperBackend(WhisperBackend):
    name = "faster-whisper"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model: Any = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError(
                    "faster-whisper is not installed. Run `pip install -r requirements.txt`, "
                    "or set WHISPER_BACKEND=openai to use the reference implementation.",
                    internal=str(exc),
                ) from exc

            settings = self._settings
            logger.info(
                "Loading Whisper model '%s' (faster-whisper, device=%s, compute=%s). "
                "The first run downloads the weights.",
                settings.whisper_model, settings.whisper_device, settings.whisper_compute_type,
            )
            try:
                self._model = WhisperModel(
                    settings.whisper_model,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                )
            except Exception as exc:  # noqa: BLE001 - model download/load failure
                raise TranscriptionError(
                    f"The Whisper model '{settings.whisper_model}' could not be loaded. "
                    "Check WHISPER_MODEL, WHISPER_DEVICE and your internet connection "
                    "(the first run downloads the weights).",
                    internal=str(exc),
                ) from exc

    def transcribe(self, audio_path: Path):
        self.load()
        settings = self._settings
        try:
            segments_iter, info = self._model.transcribe(
                str(audio_path),
                beam_size=settings.whisper_beam_size,
                language=settings.whisper_language,
                vad_filter=settings.whisper_vad_filter,
                word_timestamps=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(internal=str(exc)) from exc

        segments: List[TranscriptSegment] = []
        for index, segment in enumerate(segments_iter):  # generator: work happens here
            text = collapse_whitespace(getattr(segment, "text", ""))
            if not text:
                continue
            probability = getattr(segment, "avg_logprob", None)
            segments.append(
                TranscriptSegment(
                    segment_index=len(segments),
                    start_time=max(0.0, float(getattr(segment, "start", 0.0) or 0.0)),
                    end_time=max(0.0, float(getattr(segment, "end", 0.0) or 0.0)),
                    text=text,
                    speaker=None,
                    confidence=round(float(probability), 4) if probability is not None else None,
                )
            )

        language = getattr(info, "language", None)
        duration = getattr(info, "duration", None)
        return segments, language, duration


class OpenAIWhisperBackend(WhisperBackend):
    name = "openai"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._model: Any = None
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import whisper  # type: ignore
            except ImportError as exc:
                raise TranscriptionError(
                    "openai-whisper is not installed. Run `pip install openai-whisper`, "
                    "or set WHISPER_BACKEND=faster-whisper.",
                    internal=str(exc),
                ) from exc

            settings = self._settings
            logger.info("Loading Whisper model '%s' (openai-whisper)", settings.whisper_model)
            try:
                self._model = whisper.load_model(settings.whisper_model, device=settings.whisper_device)
            except Exception as exc:  # noqa: BLE001
                raise TranscriptionError(
                    f"The Whisper model '{settings.whisper_model}' could not be loaded.",
                    internal=str(exc),
                ) from exc

    def transcribe(self, audio_path: Path):
        self.load()
        settings = self._settings
        try:
            result = self._model.transcribe(
                str(audio_path),
                language=settings.whisper_language,
                verbose=False,
                fp16=(settings.whisper_device != "cpu"),
            )
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(internal=str(exc)) from exc

        segments: List[TranscriptSegment] = []
        for raw in result.get("segments", []):
            text = collapse_whitespace(raw.get("text", ""))
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    segment_index=len(segments),
                    start_time=max(0.0, float(raw.get("start", 0.0) or 0.0)),
                    end_time=max(0.0, float(raw.get("end", 0.0) or 0.0)),
                    text=text,
                    speaker=None,
                    confidence=(
                        round(float(raw["avg_logprob"]), 4) if raw.get("avg_logprob") is not None else None
                    ),
                )
            )

        duration = segments[-1].end_time if segments else None
        return segments, result.get("language"), duration


_BACKENDS = {
    "faster-whisper": FasterWhisperBackend,
    "openai": OpenAIWhisperBackend,
}

_backend_instance: Optional[WhisperBackend] = None
_backend_lock = threading.Lock()


def get_backend() -> WhisperBackend:
    """Process-wide cached backend so weights are loaded at most once."""
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance
    with _backend_lock:
        if _backend_instance is None:
            settings = get_settings()
            backend_cls = _BACKENDS.get(settings.whisper_backend, FasterWhisperBackend)
            _backend_instance = backend_cls()
    return _backend_instance


class TranscriptionService:
    """Turns a prepared WAV file into a transcript."""

    def __init__(self, backend: Optional[WhisperBackend] = None) -> None:
        self._settings = get_settings()
        self._backend = backend or get_backend()

    def warm_up(self) -> None:
        """Optionally pre-load the model so the first upload is not slow."""
        self._backend.load()

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        if not audio_path.exists():
            raise TranscriptionError(
                "The prepared audio file is missing.",
                internal=f"missing path {audio_path}",
            )

        started = time.perf_counter()
        segments, language, duration = self._backend.transcribe(audio_path)
        elapsed = time.perf_counter() - started

        text = build_paragraph_text(segments)
        logger.info(
            "Transcribed %s in %.1fs: %s segments, %s words, language=%s",
            audio_path.name, elapsed, len(segments), len(text.split()), language,
        )

        return TranscriptionResult(
            text=text,
            segments=segments,
            language=language,
            duration=duration,
            model=self._settings.whisper_model,
            backend=self._backend.name,
            elapsed_seconds=round(elapsed, 2),
        )


def build_paragraph_text(segments: List[TranscriptSegment], *, gap_seconds: float = 2.0) -> str:
    """Join segments into readable paragraphs.

    A new paragraph starts after a pause longer than ``gap_seconds`` or after
    roughly 500 characters, which keeps the reading view from becoming one wall
    of text.
    """
    if not segments:
        return ""

    paragraphs: List[str] = []
    current: List[str] = []
    current_length = 0
    previous_end: Optional[float] = None

    for segment in segments:
        starts_new_paragraph = (
            previous_end is not None and (segment.start_time - previous_end) >= gap_seconds
        ) or current_length > 500

        if starts_new_paragraph and current:
            paragraphs.append(" ".join(current))
            current, current_length = [], 0

        current.append(segment.text)
        current_length += len(segment.text)
        previous_end = segment.end_time

    if current:
        paragraphs.append(" ".join(current))

    return "\n\n".join(collapse_whitespace(p) for p in paragraphs if p.strip())
