"""Application error hierarchy.

Every error raised on purpose by the application carries three things:

* an HTTP status code    -> what the transport layer should answer
* a stable machine code  -> what the frontend switches on (never changes)
* a human message        -> what the user reads

Internal details (stack traces, driver messages, file paths) go to the logs via
``internal`` and are never returned to the client.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class AppError(Exception):
    """Base class for all deliberate application failures."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "Something went wrong while processing the request."

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        internal: Optional[str] = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        self.internal = internal
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --------------------------------------------------------------- 400 / 422
class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "The request contains invalid data."


class BadRequestError(AppError):
    status_code = 400
    code = "BAD_REQUEST"
    message = "The request could not be understood."


# --------------------------------------------------------------------- 404
class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "The requested resource does not exist."


class MeetingNotFoundError(NotFoundError):
    code = "MEETING_NOT_FOUND"
    message = "No meeting exists with that id."


class TranscriptNotFoundError(NotFoundError):
    code = "TRANSCRIPT_NOT_FOUND"
    message = "This meeting has no transcript yet. Run transcription first."


class IntelligenceNotFoundError(NotFoundError):
    code = "INTELLIGENCE_NOT_FOUND"
    message = "This meeting has not been analysed yet. Run the analysis first."


# ------------------------------------------------------------- file upload
class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "UNSUPPORTED_FILE_TYPE"
    message = "The uploaded file format is not supported."


class FileTooLargeError(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"
    message = "The uploaded file is larger than the allowed limit."


class EmptyFileError(AppError):
    status_code = 422
    code = "EMPTY_FILE"
    message = "The uploaded file is empty."


class CorruptedMediaError(AppError):
    status_code = 422
    code = "CORRUPTED_MEDIA"
    message = "The uploaded file could not be read as audio or video."


class InvalidFilenameError(AppError):
    status_code = 400
    code = "INVALID_FILENAME"
    message = "The uploaded filename is not allowed."


# -------------------------------------------------------------- processing
class AudioProcessingError(AppError):
    status_code = 500
    code = "AUDIO_PROCESSING_FAILED"
    message = "The audio could not be prepared for transcription."


class FFmpegNotAvailableError(AppError):
    status_code = 503
    code = "FFMPEG_NOT_AVAILABLE"
    message = "FFmpeg is not installed or not reachable on the server."


class TranscriptionError(AppError):
    status_code = 500
    code = "TRANSCRIPTION_FAILED"
    message = "Speech could not be transcribed from this recording."


class EmptyTranscriptError(AppError):
    status_code = 422
    code = "EMPTY_TRANSCRIPT"
    message = "No speech was detected in this recording."


class InvalidTranscriptError(AppError):
    status_code = 422
    code = "INVALID_TRANSCRIPT"
    message = "The generated transcript failed validation."


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "That operation conflicts with the current state of the meeting."


# --------------------------------------------------------------------- LLM
class LLMNotConfiguredError(AppError):
    status_code = 503
    code = "LLM_NOT_CONFIGURED"
    message = (
        "No AI provider is configured on the server. Set at least one of "
        "XAI_API_KEY, GEMINI_API_KEY or GROQ_API_KEY in backend/.env."
    )


class LLMRequestError(AppError):
    status_code = 502
    code = "LLM_REQUEST_FAILED"
    message = "The AI provider could not be reached."


class LLMRateLimitError(AppError):
    status_code = 429
    code = "LLM_RATE_LIMITED"
    message = "The AI provider is rate limiting requests. Try again shortly."


class LLMInvalidResponseError(AppError):
    status_code = 502
    code = "LLM_INVALID_RESPONSE"
    message = "The AI returned a response that did not match the expected format."


class LLMAllProvidersFailedError(AppError):
    """Every configured provider was tried, in order, and none produced a
    valid result. Raised by the orchestrator so the user sees one clear
    message instead of whichever vendor happened to fail last."""

    status_code = 502
    code = "LLM_ALL_PROVIDERS_FAILED"
    message = (
        "AI analysis could not be completed at this time. We attempted the "
        "configured AI providers, but none returned a valid response. "
        "Please check your API configuration or try again later."
    )


# ---------------------------------------------------------------- database
class DatabaseNotConfiguredError(AppError):
    status_code = 503
    code = "DATABASE_NOT_CONFIGURED"
    message = (
        "Supabase is not configured on the server. "
        "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env."
    )


class DatabaseError(AppError):
    status_code = 503
    code = "DATABASE_ERROR"
    message = "The database could not be reached."


class DatabaseTimeoutError(DatabaseError):
    code = "DATABASE_TIMEOUT"
    message = "The database took too long to respond."


class TableMissingError(DatabaseError):
    code = "DATABASE_TABLE_MISSING"
    message = (
        "A required database table is missing. "
        "Run backend/database/schema.sql in the Supabase SQL editor."
    )


class DuplicateRecordError(AppError):
    status_code = 409
    code = "DUPLICATE_RECORD"
    message = "That record already exists."
