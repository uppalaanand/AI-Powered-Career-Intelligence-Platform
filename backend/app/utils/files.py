"""Safe filesystem helpers for handling uploaded media.

Rules enforced here:
* uploaded names never touch the filesystem directly (path traversal defence)
* every upload lands inside a per-request directory under the configured temp root
* directories are removed after processing unless KEEP_TEMP_FILES=true
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.config import get_logger, get_settings
from app.utils.errors import InvalidFilenameError

logger = get_logger(__name__)

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")
_MAX_NAME_LENGTH = 180


def sanitize_filename(filename: Optional[str]) -> str:
    """Reduce a client-supplied filename to a safe basename.

    ``"../../etc/passwd"`` -> ``"etc_passwd"``; the result never contains a path
    separator, so it cannot escape the temp directory.
    """
    if not filename or not str(filename).strip():
        raise InvalidFilenameError("A filename is required for the upload.")

    # Strip any directory component from both POSIX and Windows style paths.
    name = str(filename).replace("\\", "/").split("/")[-1].strip()
    name = name.replace("\x00", "")
    name = _SAFE_CHARS.sub("_", name).strip("._")

    if not name:
        raise InvalidFilenameError("The uploaded filename contains no usable characters.")
    if len(name) > _MAX_NAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = _MAX_NAME_LENGTH - (len(ext) + 1 if dot else 0)
        name = (stem[:keep] + dot + ext) if dot else name[:_MAX_NAME_LENGTH]
    return name


def get_extension(filename: str) -> str:
    """Lower-case extension without the dot (``"MP4"`` -> ``"mp4"``)."""
    return Path(filename).suffix.lower().lstrip(".")


def ensure_inside(base: Path, candidate: Path) -> Path:
    """Guarantee ``candidate`` resolves inside ``base``, else refuse."""
    base_resolved = base.resolve()
    candidate_resolved = candidate.resolve()
    if base_resolved != candidate_resolved and base_resolved not in candidate_resolved.parents:
        raise InvalidFilenameError("Resolved path escapes the working directory.")
    return candidate_resolved


@contextmanager
def temporary_workspace(prefix: str = "job") -> Iterator[Path]:
    """Yield a private directory for one upload and clean it up afterwards."""
    settings = get_settings()
    workspace = settings.temp_root / f"{prefix}-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        yield workspace
    finally:
        if settings.keep_temp_files:
            logger.info("KEEP_TEMP_FILES=true, leaving workspace at %s", workspace)
        else:
            shutil.rmtree(workspace, ignore_errors=True)


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def purge_stale_workspaces(max_age_seconds: int = 6 * 60 * 60) -> int:
    """Delete leftover workspaces from crashed runs. Called at startup."""
    import time

    settings = get_settings()
    removed = 0
    now = time.time()
    try:
        for entry in settings.temp_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                if now - entry.stat().st_mtime > max_age_seconds:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:  # pragma: no cover - racing with another worker
                continue
    except OSError:  # pragma: no cover - temp root unreadable
        return 0
    if removed:
        logger.info("Removed %s stale upload workspace(s)", removed)
    return removed


def which(executable: str) -> Optional[str]:
    """Locate a binary, accepting either a bare name or an absolute path."""
    if os.path.sep in executable or (os.path.altsep and os.path.altsep in executable):
        return executable if Path(executable).exists() else None
    return shutil.which(executable)
