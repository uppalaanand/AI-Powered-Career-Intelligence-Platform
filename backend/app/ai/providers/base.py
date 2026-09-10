"""Provider contract shared by Grok, Gemini and Groq.

One interface, three implementations. Everything above this layer - the
analysis engine, the intelligence service, the controllers - asks for JSON and
never learns which vendor produced it.

    LLMProvider.complete_json(system_prompt, user_prompt) -> raw assistant text

Failure policy (deliberately quota-conscious, see docs/multi-model-fallback.md)
------------------------------------------------------------------------------
Retried inside a provider : network errors, timeouts, 5xx  - and at most
                            ``LLM_PROVIDER_MAX_ATTEMPTS`` times in total.
Never retried             : 401/403 (auth), 429 (quota), 404 (model gone), 400.
                            Those are not transient; burning a second call on
                            them wastes a free-tier request. The orchestrator
                            moves to the next provider instead.

Every failure carries a ``category`` in ``details`` so the orchestrator can log
and report *why* a provider was skipped without inspecting exception types.
"""

from __future__ import annotations

import abc
import asyncio
import random
from typing import Any, Dict, Optional

import httpx

from app.config import get_logger
from app.utils.errors import (
    AppError,
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
)

logger = get_logger(__name__)

# Only these are worth a second call: they can succeed on a retry.
TRANSIENT_STATUS = {408, 409, 425, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 8.0


class ProviderCategory:
    """Why a provider failed. Values are stable and safe to log or return."""

    NOT_CONFIGURED = "not_configured"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


#: Human wording for each category, used in logs and the final error summary.
CATEGORY_LABELS = {
    ProviderCategory.NOT_CONFIGURED: "no API key configured",
    ProviderCategory.AUTH: "authentication rejected",
    ProviderCategory.RATE_LIMIT: "rate limited or out of quota",
    ProviderCategory.MODEL_UNAVAILABLE: "configured model unavailable",
    ProviderCategory.TIMEOUT: "timed out",
    ProviderCategory.NETWORK: "network failure",
    ProviderCategory.SERVER_ERROR: "provider temporarily unavailable",
    ProviderCategory.BAD_REQUEST: "request rejected",
    ProviderCategory.INVALID_RESPONSE: "returned an unusable response",
    ProviderCategory.UNKNOWN: "failed",
}


def category_of(error: Exception) -> str:
    """Read the failure category off an exception, guessing only as a last resort."""
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        category = details.get("category")
        if isinstance(category, str) and category:
            return category

    if isinstance(error, LLMNotConfiguredError):
        return ProviderCategory.NOT_CONFIGURED
    if isinstance(error, LLMRateLimitError):
        return ProviderCategory.RATE_LIMIT
    if isinstance(error, LLMInvalidResponseError):
        return ProviderCategory.INVALID_RESPONSE
    if isinstance(error, LLMRequestError):
        return ProviderCategory.NETWORK
    return ProviderCategory.UNKNOWN


def describe(category: str) -> str:
    return CATEGORY_LABELS.get(category, CATEGORY_LABELS[ProviderCategory.UNKNOWN])


class LLMProvider(abc.ABC):
    """One AI vendor, reduced to "give me a JSON object".

    Subclasses supply :meth:`complete_json`. Everything else - naming,
    configuration checks, error shaping, the bounded retry - is shared so the
    three providers cannot drift apart.
    """

    #: Stable identifier stored in the database and returned to the frontend.
    name: str = "provider"
    #: Human name shown in logs and in the UI footnote.
    label: str = "Provider"
    #: Environment variable the operator must set for this provider to be used.
    api_key_env: str = ""

    @property
    @abc.abstractmethod
    def model(self) -> str:
        """Configured model id."""

    @property
    @abc.abstractmethod
    def is_configured(self) -> bool:
        """True when a key is present. Checked *before* any network call."""

    @abc.abstractmethod
    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4000,
        temperature: Optional[float] = None,
    ) -> str:
        """Return the raw assistant text, which the caller parses as JSON."""

    # ------------------------------------------------------------- helpers
    def not_configured_error(self) -> LLMNotConfiguredError:
        return LLMNotConfiguredError(
            f"{self.label} is not configured on the server (set {self.api_key_env}).",
            details={"provider": self.name, "category": ProviderCategory.NOT_CONFIGURED},
        )

    def _fail(
        self,
        error_cls,
        message: str,
        *,
        category: str,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        internal: Optional[str] = None,
        http_status: Optional[int] = None,
    ) -> AppError:
        details: Dict[str, Any] = {"provider": self.name, "category": category}
        if http_status is not None:
            details["status_code"] = http_status
        return error_cls(
            message,
            code=code,
            status_code=status_code,
            details=details,
            internal=internal,
        )

    def invalid_response(self, message: str, *, internal: Optional[str] = None) -> AppError:
        return self._fail(
            LLMInvalidResponseError,
            message,
            category=ProviderCategory.INVALID_RESPONSE,
            internal=internal,
        )

    # --------------------------------------------------------------- health
    async def check_connection(self) -> Dict[str, Any]:
        """One tiny live call proving the key and model work.

        Only ever triggered by the operator hitting ``/api/health/llm`` - the
        analysis path never health-checks before working.
        """
        if not self.is_configured:
            return {
                "provider": self.name,
                "label": self.label,
                "configured": False,
                "reachable": False,
                "model": self.model,
                "message": f"{self.api_key_env} is not set in backend/.env.",
            }
        try:
            await self.complete_json(
                system_prompt="You reply only with JSON.",
                user_prompt='Reply with exactly: {"ok": true}',
                max_tokens=32,
            )
            return {
                "provider": self.name,
                "label": self.label,
                "configured": True,
                "reachable": True,
                "model": self.model,
                "message": f"{self.label} responded successfully.",
            }
        except Exception as exc:  # noqa: BLE001 - health checks report, never raise
            return {
                "provider": self.name,
                "label": self.label,
                "configured": True,
                "reachable": False,
                "model": self.model,
                "message": getattr(exc, "message", str(exc)),
                "category": category_of(exc),
            }


class HttpLLMProvider(LLMProvider):
    """Shared httpx plumbing: bounded retries, backoff, error translation."""

    def __init__(self, *, timeout: float, max_attempts: int) -> None:
        self._timeout = timeout
        # Hard floor of 1: "no retries" must still mean "one request".
        self._max_attempts = max(1, int(max_attempts))

    async def _post(
        self, url: str, *, payload: Dict[str, Any], headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """POST JSON and return the decoded body, retrying only transient faults."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException as exc:
                    if attempt >= self._max_attempts:
                        raise self._fail(
                            LLMRequestError,
                            f"{self.label} did not respond in time.",
                            category=ProviderCategory.TIMEOUT,
                            code="LLM_TIMEOUT",
                            internal=str(exc),
                        ) from exc
                    await self._sleep(attempt)
                    continue
                except httpx.TransportError as exc:
                    if attempt >= self._max_attempts:
                        raise self._fail(
                            LLMRequestError,
                            f"{self.label} could not be reached. Check the network connection.",
                            category=ProviderCategory.NETWORK,
                            internal=str(exc),
                        ) from exc
                    await self._sleep(attempt)
                    continue

                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise self.invalid_response(
                            f"{self.label} returned a body that was not JSON.",
                            internal=response.text[:500],
                        ) from exc

                if response.status_code in TRANSIENT_STATUS and attempt < self._max_attempts:
                    delay = self._retry_after(response) or self._backoff(attempt)
                    logger.warning(
                        "%s returned %s (attempt %s/%s); retrying in %.1fs",
                        self.label, response.status_code, attempt, self._max_attempts, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                raise self._error_for_status(response)

        raise self.invalid_response(f"{self.label} produced no response.")  # pragma: no cover

    def _error_for_status(self, response: httpx.Response) -> AppError:
        """Map an HTTP status onto a category. No retry decisions happen here."""
        status = response.status_code
        snippet = response.text[:500]
        # Body snippets can echo request content; keep them out of INFO logs.
        logger.error("%s API error %s", self.label, status)
        logger.debug("%s error body: %s", self.label, snippet)

        if status in (401, 403):
            return self._fail(
                LLMNotConfiguredError,
                f"{self.label} rejected the API key. Check {self.api_key_env} in backend/.env.",
                category=ProviderCategory.AUTH,
                internal=snippet,
                http_status=status,
            )
        if status == 404:
            return self._fail(
                LLMRequestError,
                f"The model configured for {self.label} does not exist.",
                category=ProviderCategory.MODEL_UNAVAILABLE,
                code="LLM_MODEL_NOT_FOUND",
                internal=snippet,
                http_status=status,
            )
        if status == 429:
            return self._fail(
                LLMRateLimitError,
                f"{self.label} is rate limiting requests or the quota is exhausted.",
                category=ProviderCategory.RATE_LIMIT,
                internal=snippet,
                http_status=status,
            )
        if status >= 500:
            return self._fail(
                LLMRequestError,
                f"{self.label} is temporarily unavailable.",
                category=ProviderCategory.SERVER_ERROR,
                status_code=503,
                code="LLM_UNAVAILABLE",
                internal=snippet,
                http_status=status,
            )
        return self._fail(
            LLMRequestError,
            f"{self.label} rejected the request.",
            category=ProviderCategory.BAD_REQUEST,
            internal=snippet,
            http_status=status,
        )

    # ------------------------------------------------------------ internals
    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        header = response.headers.get("retry-after")
        if not header:
            return None
        try:
            return min(float(header), MAX_BACKOFF_SECONDS)
        except ValueError:
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, so parallel retries do not sync up."""
        return min(MAX_BACKOFF_SECONDS, (2 ** (attempt - 1)) + random.uniform(0, 0.5))

    async def _sleep(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff(attempt))
