"""File validation tests (Milestone 1: format support and invalid file handling)."""

from __future__ import annotations

import pytest

from app.models.enums import MediaKind
from app.models.media_formats import ALL_EXTENSIONS, is_supported, lookup
from app.services.file_validation_service import FileValidationService
from app.utils.errors import (
    CorruptedMediaError,
    EmptyFileError,
    FileTooLargeError,
    InvalidFilenameError,
    UnsupportedFileTypeError,
)
from app.utils.files import sanitize_filename
from tests.conftest import ffmpeg_required


@pytest.fixture
def validator() -> FileValidationService:
    return FileValidationService()


# --------------------------------------------------------------- registry
class TestFormatRegistry:
    @pytest.mark.parametrize("extension", ["mp3", "wav", "m4a", "aac", "flac", "ogg"])
    def test_required_audio_formats_supported(self, extension):
        media_format = lookup(extension)
        assert media_format is not None
        assert media_format.kind is MediaKind.AUDIO

    @pytest.mark.parametrize("extension", ["mp4", "mov", "avi", "mkv", "webm"])
    def test_required_video_formats_supported(self, extension):
        media_format = lookup(extension)
        assert media_format is not None
        assert media_format.kind is MediaKind.VIDEO

    @pytest.mark.parametrize("extension", ["pdf", "docx", "exe", "txt", "png", "zip"])
    def test_non_media_formats_rejected(self, extension):
        assert not is_supported(extension)

    def test_extension_lookup_is_case_insensitive(self):
        assert lookup("MP3") is lookup("mp3")
        assert lookup(".WAV") is lookup("wav")

    def test_every_registered_format_has_mime_types(self):
        for extension in ALL_EXTENSIONS:
            assert lookup(extension).mime_types


# ------------------------------------------------------- filename handling
class TestFilenameSafety:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("meeting.mp3", "meeting.mp3"),
            ("../../etc/passwd.mp3", "passwd.mp3"),
            ("..\\..\\windows\\system32\\evil.mp4", "evil.mp4"),
            ("/absolute/path/team sync.wav", "team_sync.wav"),
            ("weird*name?.mp3", "weird_name_.mp3"),
        ],
    )
    def test_path_traversal_is_stripped(self, raw, expected):
        result = sanitize_filename(raw)
        assert result == expected
        assert "/" not in result and "\\" not in result

    def test_empty_filename_rejected(self):
        with pytest.raises(InvalidFilenameError):
            sanitize_filename("")

    def test_very_long_filename_is_truncated(self):
        result = sanitize_filename("a" * 400 + ".mp3")
        assert len(result) <= 180
        assert result.endswith(".mp3")


# ----------------------------------------------------- metadata validation
class TestUploadMetadataValidation:
    def test_valid_audio_metadata_accepted(self, validator):
        media_format = validator.validate_upload_metadata("meeting.mp3", 5 * 1024 * 1024)
        assert media_format.kind is MediaKind.AUDIO

    def test_valid_video_metadata_accepted(self, validator):
        media_format = validator.validate_upload_metadata("standup.mp4", 20 * 1024 * 1024)
        assert media_format.kind is MediaKind.VIDEO

    def test_invalid_extension_rejected(self, validator):
        with pytest.raises(UnsupportedFileTypeError) as exc:
            validator.validate_upload_metadata("report.pdf", 1024 * 1024)
        assert exc.value.code == "UNSUPPORTED_FILE_TYPE"
        assert exc.value.status_code == 415

    def test_missing_extension_rejected(self, validator):
        with pytest.raises(UnsupportedFileTypeError):
            validator.validate_upload_metadata("recording", 1024 * 1024)

    def test_zero_byte_file_rejected(self, validator):
        with pytest.raises(EmptyFileError) as exc:
            validator.validate_upload_metadata("meeting.mp3", 0)
        assert exc.value.code == "EMPTY_FILE"

    def test_tiny_file_treated_as_empty(self, validator):
        with pytest.raises(EmptyFileError):
            validator.validate_upload_metadata("meeting.mp3", 12)

    def test_oversized_file_rejected(self, validator):
        with pytest.raises(FileTooLargeError) as exc:
            validator.validate_upload_metadata("huge.mp4", 5 * 1024 * 1024 * 1024)
        assert exc.value.status_code == 413
        assert "max_size_bytes" in exc.value.details


# -------------------------------------------------------- content checking
class TestContentValidation:
    def test_text_file_renamed_to_mp3_is_rejected(self, validator, text_file_named_mp3):
        """The whole point of not trusting the extension."""
        with pytest.raises((UnsupportedFileTypeError, CorruptedMediaError)):
            validator.validate(text_file_named_mp3, "notes.mp3")

    def test_empty_file_rejected_by_full_validation(self, validator, empty_file):
        with pytest.raises(EmptyFileError):
            validator.validate(empty_file, "empty.wav")

    @ffmpeg_required
    def test_truncated_video_rejected(self, validator, truncated_video):
        with pytest.raises((CorruptedMediaError, EmptyFileError)):
            validator.validate(truncated_video, "truncated.mp4")


# ------------------------------------------------------- real media probing
@ffmpeg_required
class TestRealMediaValidation:
    def test_valid_audio_passes_all_gates(self, validator, valid_wav):
        result = validator.validate(valid_wav, "meeting.wav")
        assert result.media_kind is MediaKind.AUDIO
        assert result.metadata.has_audio_stream is True
        assert result.metadata.duration_seconds == pytest.approx(3.0, abs=0.5)
        assert result.size_bytes > 0

    def test_valid_video_passes_all_gates(self, validator, valid_mp4):
        result = validator.validate(valid_mp4, "standup.mp4")
        assert result.media_kind is MediaKind.VIDEO
        assert result.metadata.has_audio_stream is True
        assert result.metadata.has_video_stream is True

    def test_video_without_audio_track_rejected(self, validator, silent_video):
        """Valid media, but there is nothing to transcribe - say so clearly."""
        with pytest.raises(CorruptedMediaError) as exc:
            validator.validate_media_streams(silent_video)
        assert "audio" in exc.value.message.lower()
