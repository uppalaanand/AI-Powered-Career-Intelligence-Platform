"""Supabase access gateway.

Everything that talks to the database goes through here so that:

* the service-role key is read once, from the backend environment only
* the driver is imported lazily (the app still boots without credentials)
* driver exceptions are translated into our own error codes, never leaked
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from app.config import get_logger, get_settings
from app.utils.errors import (
    AppError,
    DatabaseError,
    DatabaseNotConfiguredError,
    DatabaseTimeoutError,
    DuplicateRecordError,
    TableMissingError,
)

logger = get_logger(__name__)
T = TypeVar("T")

_client: Any = None
_lock = threading.Lock()


def is_configured() -> bool:
    return get_settings().supabase_configured


def get_client() -> Any:
    """Return a cached Supabase client, creating it on first use."""
    global _client
    settings = get_settings()

    if not settings.supabase_configured:
        raise DatabaseNotConfiguredError()

    if _client is not None:
        return _client

    with _lock:
        if _client is not None:
            return _client
        try:
            from supabase import create_client  # imported lazily on purpose
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise DatabaseError(
                "The Supabase client library is not installed. Run `pip install -r requirements.txt`.",
                internal=str(exc),
            ) from exc

        try:
            _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
            logger.info("Supabase client initialised for %s", settings.supabase_url)
        except Exception as exc:  # pragma: no cover - network/credential failure
            raise DatabaseError(
                "Could not connect to Supabase. Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
                internal=str(exc),
            ) from exc
    return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests and after credential changes."""
    global _client
    with _lock:
        _client = None


def table(name: str) -> Any:
    return get_client().table(name)


def run(operation: Callable[[], T], *, action: str) -> T:
    """Execute a database call and convert driver failures into AppErrors."""
    try:
        return operation()
    except AppError:
        # Already one of ours (e.g. "not configured"); do not re-wrap it.
        raise
    except Exception as exc:  # noqa: BLE001 - we deliberately catch driver errors
        raise _translate(exc, action) from exc


def _translate(exc: Exception, action: str) -> DatabaseError:
    text = str(exc).lower()
    logger.error("Database operation failed (%s): %s", action, exc)

    if "timed out" in text or "timeout" in text:
        return DatabaseTimeoutError(details={"action": action}, internal=str(exc))
    if "does not exist" in text or "could not find the table" in text or "42p01" in text:
        return TableMissingError(details={"action": action}, internal=str(exc))
    if "duplicate key" in text or "23505" in text:
        return DuplicateRecordError(
            "That record already exists.", details={"action": action}, internal=str(exc)
        )
    if "jwt" in text or "invalid api key" in text or "unauthorized" in text or "401" in text:
        return DatabaseError(
            "Supabase rejected the credentials. Check SUPABASE_SERVICE_ROLE_KEY.",
            code="DATABASE_AUTH_FAILED",
            details={"action": action},
            internal=str(exc),
        )
    if "connection" in text or "name or service not known" in text or "getaddrinfo" in text:
        return DatabaseError(
            "The database could not be reached. Check SUPABASE_URL and your network.",
            details={"action": action},
            internal=str(exc),
        )
    return DatabaseError(
        "The database rejected the request.",
        details={"action": action},
        internal=str(exc),
    )


def ping() -> bool:
    """Cheap connectivity + schema check used by /api/health."""
    try:
        run(lambda: table("meetings").select("id").limit(1).execute(), action="ping")
        return True
    except Exception as exc:  # noqa: BLE001 - health check must never raise
        logger.warning("Supabase health check failed: %s", exc)
        return False
