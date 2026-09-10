"""Shared test fixtures.

Media fixtures are generated with FFmpeg at test time rather than committed as
binaries, so the repository stays small and the fixtures always match the local
FFmpeg build. Tests that need FFmpeg skip themselves when it is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

FFMPEG = shutil.which("ffmpeg")
ffmpeg_required = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg is not installed")


def _run_ffmpeg(args: list[str]) -> bool:
    if FFMPEG is None:
        return False
    result = subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace"
    )
    return result.returncode == 0


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("media")


@pytest.fixture(scope="session")
def valid_wav(media_dir: Path) -> Path:
    """Three seconds of real, decodable audio."""
    path = media_dir / "meeting.wav"
    if not path.exists():
        _run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-ar", "44100", str(path)])
    return path


@pytest.fixture(scope="session")
def valid_mp4(media_dir: Path) -> Path:
    """A short video that really contains both a video and an audio stream."""
    path = media_dir / "meeting.mp4"
    if not path.exists():
        _run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ])
    return path


@pytest.fixture(scope="session")
def silent_video(media_dir: Path) -> Path:
    """Video with no audio track: valid media, but nothing to transcribe."""
    path = media_dir / "silent.mp4"
    if not path.exists():
        _run_ffmpeg([
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-c:v", "libx264", str(path),
        ])
    return path


@pytest.fixture
def empty_file(tmp_path: Path) -> Path:
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")
    return path


@pytest.fixture
def text_file_named_mp3(tmp_path: Path) -> Path:
    """A text file renamed to .mp3 - the classic "trust the extension" trap."""
    path = tmp_path / "notes.mp3"
    path.write_text("This is a text document pretending to be audio. " * 60)
    return path


@pytest.fixture
def truncated_video(tmp_path: Path, valid_mp4: Path) -> Path:
    """The first 2 KB of a real MP4: right header, unusable file."""
    path = tmp_path / "truncated.mp4"
    path.write_bytes(valid_mp4.read_bytes()[:2000] if valid_mp4.exists() else b"\x00" * 2000)
    return path


@pytest.fixture
def sample_transcript_text() -> str:
    return (
        "Welcome everyone. Today we are discussing the mobile application launch. "
        "Ravi will handle the API integration and finish it by Friday. "
        "Priya will prepare the UI testing report. "
        "We agreed to continue with the planned launch date."
    )
