"""Shared async HTTP client - one connection pool per event loop.

Opening a new HTTPS connection for every request costs a full TCP + TLS
handshake each time. Measured against Pinecone from this project's development
machine, a similarity query took a median **1229 ms** on a fresh connection and
**332 ms** on a reused one - the difference that keeps semantic search well
inside its three-second budget. Groq calls benefit the same way.

The pool is keyed by event loop because an ``httpx.AsyncClient`` is bound to the
loop it was first used on; FastAPI runs one loop, while each async test gets its
own. Per-request timeouts are passed at the call site, so callers keep their own
limits.
"""

from __future__ import annotations

import asyncio
import weakref

import httpx

_clients: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)

_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60)


def shared_async_client() -> httpx.AsyncClient:
    """The pooled client for the running event loop, created on first use."""
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(timeout=30.0, limits=_LIMITS)
        _clients[loop] = client
    return client


async def close_shared_client() -> None:
    """Close the running loop's pool. Called when the application shuts down."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    client = _clients.pop(loop, None)
    if client is not None and not client.is_closed:
        await client.aclose()
