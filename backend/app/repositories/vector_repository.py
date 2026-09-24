"""Pinecone vector index - the only file that knows Pinecone exists.

Supabase stays the source of truth for meetings; Pinecone is purely a search
index over that data. Nothing in here is authoritative: every vector can be
rebuilt from the database, which is why a failed upsert never costs data.

Talking to Pinecone over REST with ``httpx`` rather than the SDK keeps the
dependency list unchanged and matches how the LLM providers are written. Two
planes are involved:

    control  https://api.pinecone.io        describe/create the index
    data     https://{index_host}           upsert / query / delete vectors

The host is discovered once per process from the control plane and cached; set
``PINECONE_INDEX_HOST`` to skip even that call.

Deletion note
-------------
Serverless indexes (what a new Pinecone account gets) **cannot delete by
metadata filter**. So deleting a meeting lists its vector ids by the
``{meeting_id}#`` prefix and deletes those - which is exactly why the ids are
built with that prefix in ``app.knowledge.documents``. ``delete_all`` is not
exposed here at all: no ordinary application action should be able to wipe the
index.
"""

from __future__ import annotations

import asyncio
import random
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.config import get_logger, get_settings
from app.utils.http import shared_async_client
from app.utils.errors import (
    VectorDimensionMismatchError,
    VectorStoreError,
    VectorStoreNotConfiguredError,
)

logger = get_logger(__name__)

API_VERSION = "2025-01"
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 8.0
#: Pinecone caps an upsert request; batching keeps bodies well inside the limit.
UPSERT_BATCH_SIZE = 96
#: Ids accepted by a single delete call.
DELETE_BATCH_SIZE = 500
#: How long to wait for a newly created index to become Ready (30 x 2 s).
INDEX_READY_POLLS = 30
INDEX_READY_POLL_SECONDS = 2.0

_host_cache: Dict[str, str] = {}
_host_lock = threading.Lock()


@dataclass
class VectorMatch:
    """One similarity hit, with the metadata that traces it back to a meeting."""

    id: str
    score: float
    metadata: Dict[str, Any]

    @property
    def meeting_id(self) -> str:
        return str(self.metadata.get("meeting_id") or "")

    @property
    def source_type(self) -> str:
        return str(self.metadata.get("source_type") or "")

    @property
    def content(self) -> str:
        return str(self.metadata.get("content") or "")


@dataclass
class VectorRecord:
    """A vector ready to store. ``id`` is deterministic, so upsert = update."""

    id: str
    values: List[float]
    metadata: Dict[str, Any]


class VectorRepository:
    """CRUD + similarity search over the meeting knowledge index."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        index_name: Optional[str] = None,
        namespace: Optional[str] = None,
        host: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.pinecone_api_key
        self._index_name = index_name or settings.pinecone_index_name
        self._namespace = namespace or settings.pinecone_namespace
        self._host = host or settings.pinecone_index_host
        self._control_url = settings.pinecone_base_url.rstrip("/")
        self._timeout = settings.pinecone_timeout_seconds
        self._max_attempts = max(1, settings.pinecone_max_attempts)
        self._dimensions = settings.embedding_dimensions
        self._cloud = settings.pinecone_cloud
        self._region = settings.pinecone_region
        self._create_if_missing = settings.pinecone_create_index_if_missing

    # -------------------------------------------------------- configuration
    @property
    def index_name(self) -> str:
        return self._index_name

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def is_configured(self) -> bool:
        """Local check only - never a probe request."""
        return bool(self._api_key)

    def _require_configured(self) -> None:
        if not self.is_configured:
            raise VectorStoreNotConfiguredError()

    # ---------------------------------------------------------------- write
    async def upsert(self, records: Sequence[VectorRecord]) -> int:
        """Insert or update vectors. Deterministic ids make this idempotent."""
        self._require_configured()
        if not records:
            return 0

        self._check_dimensions(records)
        host = await self._resolve_host()
        written = 0

        for start in range(0, len(records), UPSERT_BATCH_SIZE):
            batch = records[start : start + UPSERT_BATCH_SIZE]
            payload = {
                "namespace": self._namespace,
                "vectors": [
                    {"id": record.id, "values": record.values, "metadata": record.metadata}
                    for record in batch
                ],
            }
            await self._post(f"https://{host}/vectors/upsert", payload)
            written += len(batch)

        logger.info(
            "Upserted %s vector(s) into index '%s' namespace '%s'",
            written, self._index_name, self._namespace,
        )
        return written

    async def delete_by_ids(self, ids: Sequence[str]) -> int:
        """Delete specific vectors. Safe no-op when the list is empty."""
        self._require_configured()
        if not ids:
            return 0

        host = await self._resolve_host()
        deleted = 0
        for start in range(0, len(ids), DELETE_BATCH_SIZE):
            batch = list(ids[start : start + DELETE_BATCH_SIZE])
            await self._post(
                f"https://{host}/vectors/delete",
                {"ids": batch, "namespace": self._namespace},
            )
            deleted += len(batch)

        logger.info("Deleted %s vector(s) from namespace '%s'", deleted, self._namespace)
        return deleted

    async def delete_by_meeting(self, meeting_id: str) -> int:
        """Remove every vector belonging to one meeting.

        Listing by id prefix first is what makes this work on serverless
        indexes, where metadata-filter deletes are unsupported. It also keeps
        the blast radius to a single meeting by construction.
        """
        self._require_configured()
        if not meeting_id:
            return 0
        ids = await self.list_ids_for_meeting(meeting_id)
        if not ids:
            logger.info("No vectors found for meeting %s; nothing to delete.", meeting_id)
            return 0
        return await self.delete_by_ids(ids)

    async def list_ids_for_meeting(self, meeting_id: str) -> List[str]:
        """Every vector id whose prefix is this meeting, following pagination."""
        self._require_configured()
        host = await self._resolve_host()
        prefix = f"{meeting_id}#"
        ids: List[str] = []
        token: Optional[str] = None

        while True:
            params: Dict[str, Any] = {
                "prefix": prefix,
                "namespace": self._namespace,
                "limit": 100,
            }
            if token:
                params["paginationToken"] = token
            body = await self._get(f"https://{host}/vectors/list", params)
            ids.extend(
                str(item.get("id"))
                for item in (body.get("vectors") or [])
                if item.get("id")
            )
            token = ((body.get("pagination") or {}).get("next")) or None
            if not token:
                break

        return ids

    # ----------------------------------------------------------------- read
    async def query(
        self,
        vector: Sequence[float],
        *,
        top_k: int = 8,
        metadata_filter: Optional[Dict[str, Any]] = None,
        include_values: bool = False,
    ) -> List[VectorMatch]:
        """Similarity search. One request, no LLM involved."""
        self._require_configured()
        if not vector:
            raise VectorStoreError(
                "Cannot search with an empty query vector.",
                code="VECTOR_EMPTY_QUERY",
                status_code=422,
            )

        host = await self._resolve_host()
        payload: Dict[str, Any] = {
            "vector": list(vector),
            "topK": max(1, int(top_k)),
            "namespace": self._namespace,
            "includeMetadata": True,
            "includeValues": include_values,
        }
        if metadata_filter:
            payload["filter"] = metadata_filter

        body = await self._post(f"https://{host}/query", payload)
        matches = [
            VectorMatch(
                id=str(match.get("id") or ""),
                score=float(match.get("score") or 0.0),
                metadata=dict(match.get("metadata") or {}),
            )
            for match in (body.get("matches") or [])
        ]
        logger.info(
            "Vector search returned %s match(es) from namespace '%s'",
            len(matches), self._namespace,
        )
        return matches

    async def stats(self) -> Dict[str, Any]:
        """Index statistics. Used by the health endpoint, never by search."""
        self._require_configured()
        host = await self._resolve_host()
        body = await self._post(f"https://{host}/describe_index_stats", {})
        namespaces = body.get("namespaces") or {}
        mine = namespaces.get(self._namespace) or {}
        return {
            "index": self._index_name,
            "namespace": self._namespace,
            "dimension": body.get("dimension"),
            "vector_count": mine.get("vectorCount", 0),
            "total_vector_count": body.get("totalVectorCount", 0),
        }

    async def check_connection(self) -> Dict[str, Any]:
        """Operator-triggered connectivity report for ``/api/health/vector``."""
        if not self.is_configured:
            return {
                "configured": False,
                "reachable": False,
                "index": self._index_name,
                "namespace": self._namespace,
                "message": "PINECONE_API_KEY is not set in backend/.env.",
            }
        try:
            stats = await self.stats()
            dimension = stats.get("dimension")
            message = (
                f"Pinecone index '{self._index_name}' is reachable with "
                f"{stats.get('vector_count', 0)} vector(s) in namespace "
                f"'{self._namespace}'."
            )
            if dimension and int(dimension) != self._dimensions:
                message = (
                    f"Index dimension is {dimension} but EMBEDDING_DIMENSIONS is "
                    f"{self._dimensions}. Recreate the index with dimension "
                    f"{self._dimensions}, or fix the setting."
                )
                return {
                    "configured": True, "reachable": True, "healthy": False,
                    "index": self._index_name, "namespace": self._namespace,
                    "message": message, **stats,
                }
            return {
                "configured": True, "reachable": True, "healthy": True,
                "message": message, **stats,
            }
        except Exception as exc:  # noqa: BLE001 - health checks report, never raise
            return {
                "configured": True,
                "reachable": False,
                "index": self._index_name,
                "namespace": self._namespace,
                "message": getattr(exc, "message", str(exc)),
            }

    # ------------------------------------------------------------ internals
    def _check_dimensions(self, records: Sequence[VectorRecord]) -> None:
        """Catch a model/index mismatch here, with a message that says what to
        fix, instead of letting Pinecone answer with a bare 400."""
        for record in records:
            if len(record.values) != self._dimensions:
                raise VectorDimensionMismatchError(
                    f"Embedding has {len(record.values)} dimensions but the index "
                    f"expects {self._dimensions}.",
                    details={
                        "expected": self._dimensions,
                        "received": len(record.values),
                    },
                )

    async def _resolve_host(self) -> str:
        """Find the index's data-plane host, creating the index if allowed."""
        if self._host:
            return self._host

        cached = _host_cache.get(self._index_name)
        if cached:
            self._host = cached
            return cached

        host = await self._describe_index_host()
        if host is None and self._create_if_missing:
            host = await self._create_index()
        if not host:
            raise VectorStoreError(
                f"The Pinecone index '{self._index_name}' does not exist. Create it "
                f"with dimension {self._dimensions} and the cosine metric, or set "
                "PINECONE_CREATE_INDEX_IF_MISSING=true.",
                code="VECTOR_INDEX_MISSING",
                status_code=503,
            )

        with _host_lock:
            _host_cache[self._index_name] = host
        self._host = host
        return host

    async def _describe_index_host(self, *, require_ready: bool = False) -> Optional[str]:
        """The index's data-plane host, or None if it does not exist (yet).

        Also the place where a dimension mismatch is caught: an index built for a
        different embedding model is refused with a message naming both sizes,
        instead of letting Pinecone reject every upsert with a bare 400.
        """
        body = await self._get(
            f"{self._control_url}/indexes/{self._index_name}", None, allow_404=True
        )
        if body is None:
            return None
        host = body.get("host")
        dimension = body.get("dimension")
        if dimension and int(dimension) != self._dimensions:
            raise VectorDimensionMismatchError(
                f"Pinecone index '{self._index_name}' has dimension {dimension}, but the "
                f"embedding model produces {self._dimensions}. Point PINECONE_INDEX_NAME at "
                f"an index of dimension {self._dimensions} (it is created automatically if "
                "missing). Existing indexes are never modified or deleted by this app.",
                details={"index_dimension": int(dimension), "expected": self._dimensions},
            )
        if require_ready and not (body.get("status") or {}).get("ready"):
            return None
        return str(host) if host else None

    async def _create_index(self) -> Optional[str]:
        """Create a serverless index sized for the configured embedding model."""
        logger.info(
            "Creating Pinecone index '%s' (dimension=%s, metric=cosine, %s/%s)",
            self._index_name, self._dimensions, self._cloud, self._region,
        )
        payload = {
            "name": self._index_name,
            "dimension": self._dimensions,
            "metric": "cosine",
            "spec": {"serverless": {"cloud": self._cloud, "region": self._region}},
        }
        try:
            body = await self._post(f"{self._control_url}/indexes", payload)
        except VectorStoreError as exc:
            # A parallel worker may have won the race; re-read before failing.
            if exc.details.get("status_code") == 409:
                return await self._describe_index_host()
            raise

        # A brand-new serverless index reports a host at once but only accepts
        # writes once it is Ready - usually within a few seconds. Wait for that,
        # so the very first indexing run does not fail on a half-created index.
        for _ in range(INDEX_READY_POLLS):
            ready_host = await self._describe_index_host(require_ready=True)
            if ready_host:
                logger.info("Pinecone index '%s' is ready.", self._index_name)
                return ready_host
            await asyncio.sleep(INDEX_READY_POLL_SECONDS)
        raise VectorStoreError(
            f"The Pinecone index '{self._index_name}' was created but is not ready yet. "
            "Try again in a minute.",
            code="VECTOR_INDEX_NOT_READY", status_code=503,
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Api-Key": self._api_key or "",
            "X-Pinecone-API-Version": API_VERSION,
            "Content-Type": "application/json",
        }

    async def _post(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", url, json=payload)

    async def _get(
        self,
        url: str,
        params: Optional[Dict[str, Any]],
        *,
        allow_404: bool = False,
    ) -> Any:
        return await self._request("GET", url, params=params, allow_404=allow_404)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        allow_404: bool = False,
    ) -> Any:
        self._require_configured()
        # One pooled connection per event loop: reusing it skips a TLS handshake
        # per request (Pinecone query: ~1.2 s -> ~0.3 s measured).
        client = shared_async_client()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await client.request(
                    method, url, json=json, params=params, headers=self._headers(),
                    timeout=self._timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt >= self._max_attempts:
                    raise VectorStoreError(
                        "The meeting search index could not be reached. "
                        "Check PINECONE_API_KEY and your network.",
                        internal=str(exc),
                    ) from exc
                await self._sleep(attempt)
                continue

            if response.status_code in (200, 201, 202):
                try:
                    return response.json()
                except ValueError:
                    return {}

            if response.status_code == 404 and allow_404:
                return None

            if response.status_code in TRANSIENT_STATUS and attempt < self._max_attempts:
                logger.warning(
                    "Pinecone returned %s (attempt %s/%s); retrying.",
                    response.status_code, attempt, self._max_attempts,
                )
                await self._sleep(attempt)
                continue

            raise self._error_for(response)

        raise VectorStoreError()  # pragma: no cover - loop always returns or raises

    def _error_for(self, response: httpx.Response) -> VectorStoreError:
        status = response.status_code
        snippet = response.text[:300]
        logger.error("Pinecone API error %s", status)
        logger.debug("Pinecone error body: %s", snippet)

        if status in (401, 403):
            return VectorStoreNotConfiguredError(
                "Pinecone rejected the API key. Check PINECONE_API_KEY in backend/.env.",
                internal=snippet,
            )
        if status == 404:
            return VectorStoreError(
                f"The Pinecone index '{self._index_name}' was not found. Check "
                "PINECONE_INDEX_NAME in backend/.env.",
                code="VECTOR_INDEX_MISSING",
                status_code=503,
                internal=snippet,
            )
        if status == 429:
            return VectorStoreError(
                "The vector database is rate limiting requests. Try again shortly.",
                code="VECTOR_RATE_LIMITED",
                status_code=429,
                internal=snippet,
            )
        return VectorStoreError(
            "The vector database rejected the request.",
            details={"status_code": status},
            internal=snippet,
        )

    @staticmethod
    async def _sleep(attempt: int) -> None:
        delay = min(MAX_BACKOFF_SECONDS, (2 ** (attempt - 1)) + random.uniform(0, 0.4))
        await asyncio.sleep(delay)


def reset_host_cache() -> None:
    """Drop the cached data-plane host. Used by tests and after reconfiguring."""
    with _host_lock:
        _host_cache.clear()
