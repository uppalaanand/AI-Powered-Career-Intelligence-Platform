"""API-level tests using FastAPI's TestClient.

These exercise the response envelope, status codes and error handling without a
database: endpoints that need Supabase are expected to answer 503 with a clear
code, which is itself the behaviour worth testing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import ffmpeg_required


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


class TestSystemEndpoints:
    def test_root_banner(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_health_reports_configuration(self, client):
        body = client.get("/api/health").json()
        assert body["success"] is True
        data = body["data"]
        assert data["status"] in ("ok", "degraded")
        assert "whisper_model" in data
        assert isinstance(data["warnings"], list)

    def test_supported_formats_lists_required_types(self, client):
        data = client.get("/api/config/formats").json()["data"]
        for extension in ("mp3", "wav", "m4a", "aac", "flac", "ogg"):
            assert extension in data["audio"]
        for extension in ("mp4", "mov", "avi", "mkv", "webm"):
            assert extension in data["video"]
        assert data["accept_attribute"].startswith(".")

    def test_openapi_document_is_generated(self, client):
        schema = client.get("/openapi.json").json()
        assert "/api/meetings/upload" in schema["paths"]
        assert "/api/transcription/accuracy" in schema["paths"]

    def test_unknown_route_returns_the_error_envelope(self, client):
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "NOT_FOUND"

    def test_every_response_carries_a_request_id(self, client):
        response = client.get("/api/health")
        assert response.headers.get("X-Request-ID")


class TestUploadValidationThroughApi:
    def test_upload_without_a_file_is_rejected(self, client):
        response = client.post("/api/meetings/upload")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_unsupported_extension_rejected(self, client):
        response = client.post(
            "/api/meetings/upload",
            files={"file": ("report.pdf", b"%PDF-1.7 fake pdf content" * 100, "application/pdf")},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    def test_empty_file_rejected(self, client):
        response = client.post(
            "/api/meetings/upload", files={"file": ("empty.mp3", b"", "audio/mpeg")}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "EMPTY_FILE"

    def test_text_file_renamed_to_audio_rejected(self, client):
        payload = b"This is not audio, it is plain text pretending. " * 60
        response = client.post(
            "/api/meetings/upload", files={"file": ("fake.mp3", payload, "audio/mpeg")}
        )
        assert response.status_code in (415, 422)
        assert response.json()["error"]["code"] in ("UNSUPPORTED_FILE_TYPE", "CORRUPTED_MEDIA")

    @ffmpeg_required
    def test_valid_audio_reaches_the_database_layer(self, valid_wav, client):
        """With no Supabase configured this must fail loudly and specifically,
        never silently pretend the meeting was saved."""
        response = client.post(
            "/api/meetings/upload",
            files={"file": ("meeting.wav", valid_wav.read_bytes(), "audio/wav")},
        )
        assert response.status_code in (201, 503)
        if response.status_code == 503:
            assert response.json()["error"]["code"] in (
                "DATABASE_NOT_CONFIGURED", "DATABASE_ERROR"
            )


class TestAccuracyEndpoint:
    def test_identical_transcripts_score_100(self, client):
        text = "The team discussed the mobile application launch."
        response = client.post(
            "/api/transcription/accuracy",
            json={"reference_transcript": text, "hypothesis_transcript": text},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["accuracy_percentage"] == 100.0
        assert data["status"] == "PASSED"

    def test_errors_are_classified_in_the_response(self, client):
        response = client.post(
            "/api/transcription/accuracy",
            json={
                "reference_transcript": "ravi will finish the api integration by friday",
                "hypothesis_transcript": "robbie will finish integration by friday today",
            },
        )
        data = response.json()["data"]
        assert data["substitutions"] >= 1
        assert data["deletions"] >= 1
        assert data["insertions"] >= 1
        assert data["status"] == "NEEDS IMPROVEMENT"
        assert data["missing_words"] and data["extra_words"]

    def test_blank_reference_is_rejected(self, client):
        response = client.post(
            "/api/transcription/accuracy",
            json={"reference_transcript": "   ", "hypothesis_transcript": "something"},
        )
        assert response.status_code == 422

    def test_missing_hypothesis_and_meeting_id_is_rejected(self, client):
        response = client.post(
            "/api/transcription/accuracy",
            json={"reference_transcript": "a real reference transcript"},
        )
        assert response.status_code in (400, 422, 503)
        assert response.json()["success"] is False

    def test_custom_target_is_honoured(self, client):
        response = client.post(
            "/api/transcription/accuracy",
            json={
                "reference_transcript": "one two three four",
                "hypothesis_transcript": "one two three five",
                "target_accuracy": 50,
            },
        )
        data = response.json()["data"]
        assert data["target_accuracy"] == 50
        assert data["passed"] is True
