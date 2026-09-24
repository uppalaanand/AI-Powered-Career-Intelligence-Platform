"""Groq - the application's one and only LLM provider.

This is the **only file in the project that talks to an LLM**. Every piece of
language-model work - meeting summaries, key points, decisions, action items,
participants, deadlines, priorities, and Milestone 3's RAG answers - reaches
Groq through :class:`GroqClient`, via ``app.ai.llm_service.LLMService``.

There is deliberately no fallback provider. Groq is the provider.

Protocol
--------
Groq exposes the OpenAI-compatible Chat Completions API::

    POST https://api.groq.com/openai/v1/chat/completions
    Authorization: Bearer $GROQ_API_KEY

and supports ``response_format: {"type": "json_object"}`` (JSON mode), which
this client always requests because every caller wants one JSON object back.

Retry policy - quota-conscious on purpose
-----------------------------------------
Retried (at most ``GROQ_MAX_ATTEMPTS`` requests in total):
    network errors, timeouts, 5xx, and Groq's 498 "capacity exceeded".
Retried once, only if Groq says the wait is short:
    429 rate limit, when the ``retry-after`` header is within
    ``GROQ_MAX_RATE_LIMIT_WAIT_SECONDS``. A long wait means a daily quota is
    exhausted, and waiting would only hang the request.
Never retried:
    401/403 (bad key), 404 (unknown model), 413 (request too large for the
    plan), and other 400s. Retrying those only spends quota to get the same
    answer.

Two 400s are handled specially, both to *save* requests:

* ``json_validate_failed`` - Groq rejected the model's JSON but returns the
  text it generated. That text is handed back to the caller, whose parser
  repairs common slips (trailing commas, stray prose). Only if that fails is
  another request made.
* a rejected ``response_format`` or ``reasoning_effort`` parameter - retried
  once without it, so switching ``GROQ_MODEL`` never breaks analysis.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, Optional

import httpx

from app.config import get_logger, get_settings
from app.utils.http import shared_async_client
from app.utils.errors import (
    AppError,
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
)

logger = get_logger(__name__)

#: Worth a second request: these can succeed moments later. 498 is Groq's
#: "flex tier capacity exceeded".
TRANSIENT_STATUS = {408, 409, 425, 498, 500, 502, 503, 504}
MAX_BACKOFF_SECONDS = 8.0

#: Model families that accept ``reasoning_effort``. Sending it to any other
#: model is a 400, so it is only added for these.
REASONING_MODEL_PREFIXES = ("openai/gpt-oss", "qwen/qwen3")


class ErrorCategory:
    """Why a Groq request failed. Stable values, safe to log or return."""

    NOT_CONFIGURED = "not_configured"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    REQUEST_TOO_LARGE = "request_too_large"
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


_CATEGORY_LABELS = {
    ErrorCategory.NOT_CONFIGURED: "no API key configured",
    ErrorCategory.AUTH: "authentication rejected",
    ErrorCategory.RATE_LIMIT: "rate limited or out of quota",
    ErrorCategory.REQUEST_TOO_LARGE: "request too large for the plan's token limit",
    ErrorCategory.MODEL_UNAVAILABLE: "configured model unavailable",
    ErrorCategory.TIMEOUT: "timed out",
    ErrorCategory.NETWORK: "network failure",
    ErrorCategory.SERVER_ERROR: "temporarily unavailable",
    ErrorCategory.BAD_REQUEST: "request rejected",
    ErrorCategory.INVALID_RESPONSE: "returned an unusable response",
    ErrorCategory.UNKNOWN: "failed",
}


def category_of(error: Exception) -> str:
    """Read the failure category off an exception, guessing only as a last resort."""
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        category = details.get("category")
        if isinstance(category, str) and category:
            return category
    if isinstance(error, LLMNotConfiguredError):
        return ErrorCategory.NOT_CONFIGURED
    if isinstance(error, LLMRateLimitError):
        return ErrorCategory.RATE_LIMIT
    if isinstance(error, LLMInvalidResponseError):
        return ErrorCategory.INVALID_RESPONSE
    if isinstance(error, LLMRequestError):
        return ErrorCategory.NETWORK
    return ErrorCategory.UNKNOWN


def describe(category: str) -> str:
    return _CATEGORY_LABELS.get(category, _CATEGORY_LABELS[ErrorCategory.UNKNOWN])


class GroqClient:
    """Async JSON-mode chat completions against Groq."""

    name = "groq"
    label = "Groq"
    api_key_env = "GROQ_API_KEY"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.groq_api_key
        self._model = model or settings.groq_model
        self._base_url = (base_url or settings.groq_base_url).rstrip("/")
        self._timeout = settings.groq_timeout_seconds
        # Floor of 1: "no retries" must still mean "one request".
        self._max_attempts = max(1, settings.groq_max_attempts)
        self._temperature = settings.groq_temperature
        self._reasoning_effort = settings.groq_reasoning_effort
        self._max_rate_limit_wait = max(0.0, settings.groq_max_rate_limit_wait_seconds)

    # ---------------------------------------------------------- properties
    @property
    def model(self) -> str:
        return self._model

    @property
    def is_configured(self) -> bool:
        """Local check only - never a probe request."""
        return bool(self._api_key)

    @property
    def uses_reasoning_effort(self) -> bool:
        return bool(self._reasoning_effort) and self._model.startswith(REASONING_MODEL_PREFIXES)

    # ---------------------------------------------------------------- call
    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2000,
        temperature: Optional[float] = None,
    ) -> str:
        """Ask for one JSON object and return the raw assistant text.

        The caller parses and validates it; this method only guarantees that
        whatever comes back is non-empty text or a classified error.
        """
        if not self.is_configured:
            raise self._fail(
                LLMNotConfiguredError,
                "Groq is not configured on the server (set GROQ_API_KEY in backend/.env).",
                category=ErrorCategory.NOT_CONFIGURED,
            )

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._temperature if temperature is None else temperature,
            # Reasoning models count their reasoning against this budget too,
            # which is why reasoning_effort defaults to "low" below.
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.uses_reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort

        try:
            body = await self._post(payload)
        except _SalvagedGeneration as salvaged:
            return salvaged.text
        except LLMRequestError as exc:
            dropped = self._drop_rejected_parameter(payload, exc)
            if not dropped:
                raise
            logger.warning("Groq rejected '%s' for model %s; retrying once without it.",
                           dropped, self._model)
            try:
                body = await self._post(payload)
            except _SalvagedGeneration as salvaged:
                return salvaged.text

        return self._extract_content(body)

    # -------------------------------------------------------------- health
    async def check_connection(self) -> Dict[str, Any]:
        """One tiny live call. Only ever triggered by ``/api/health/llm``."""
        if not self.is_configured:
            return {
                "provider": self.name,
                "configured": False,
                "reachable": False,
                "model": self._model,
                "message": "GROQ_API_KEY is not set in backend/.env.",
            }
        try:
            await self.complete_json(
                system_prompt="You reply only with JSON.",
                user_prompt='Reply with exactly this JSON object: {"ok": true}',
                max_tokens=64,
            )
            return {
                "provider": self.name,
                "configured": True,
                "reachable": True,
                "model": self._model,
                "message": "Groq responded successfully.",
            }
        except Exception as exc:  # noqa: BLE001 - health checks report, never raise
            return {
                "provider": self.name,
                "configured": True,
                "reachable": False,
                "model": self._model,
                "message": getattr(exc, "message", str(exc)),
                "category": category_of(exc),
            }

    # ----------------------------------------------------------- transport
    async def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        rate_limit_retry_used = False

        # One pooled connection per event loop: reusing it skips a TLS handshake
        # per request (Pinecone query: ~1.2 s -> ~0.3 s measured).
        client = shared_async_client()
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await client.post(
                    url, json=payload, headers=headers, timeout=self._timeout
                )
            except httpx.TimeoutException as exc:
                if attempt >= self._max_attempts:
                    raise self._fail(
                        LLMRequestError, "Groq did not respond in time. Try again shortly.",
                        category=ErrorCategory.TIMEOUT, code="LLM_TIMEOUT",
                        internal=str(exc),
                    ) from exc
                await self._sleep(self._backoff(attempt))
                continue
            except httpx.TransportError as exc:
                if attempt >= self._max_attempts:
                    raise self._fail(
                        LLMRequestError,
                        "Groq could not be reached. Check the network connection.",
                        category=ErrorCategory.NETWORK, internal=str(exc),
                    ) from exc
                await self._sleep(self._backoff(attempt))
                continue

            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise self._fail(
                        LLMInvalidResponseError, "Groq returned a body that was not JSON.",
                        category=ErrorCategory.INVALID_RESPONSE,
                        internal=response.text[:300],
                    ) from exc

            if status in TRANSIENT_STATUS and attempt < self._max_attempts:
                delay = self._retry_after(response) or self._backoff(attempt)
                logger.warning("Groq returned %s (attempt %s/%s); retrying in %.1fs",
                               status, attempt, self._max_attempts, delay)
                await self._sleep(min(delay, MAX_BACKOFF_SECONDS))
                continue

            if status == 429 and not rate_limit_retry_used and attempt < self._max_attempts:
                wait = self._retry_after(response)
                if wait is not None and wait <= self._max_rate_limit_wait:
                    rate_limit_retry_used = True
                    logger.warning("Groq rate limit hit; waiting %.1fs as instructed, "
                                   "then retrying once.", wait)
                    await self._sleep(wait)
                    continue

            raise self._error_for(response)

        raise self._fail(  # pragma: no cover - the loop always returns or raises
            LLMRequestError, "Groq produced no response.", category=ErrorCategory.UNKNOWN
        )

    # ------------------------------------------------------------- parsing
    def _extract_content(self, body: Dict[str, Any]) -> str:
        try:
            choice = body["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise self._fail(
                LLMInvalidResponseError, "The Groq response was missing the expected content.",
                category=ErrorCategory.INVALID_RESPONSE, internal=str(body)[:400],
            ) from exc

        self._log_usage(body)

        if not content or not str(content).strip():
            reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            hint = (
                " The output token budget ran out before the answer was written."
                if reason == "length" else ""
            )
            raise self._fail(
                LLMInvalidResponseError, f"Groq returned an empty response.{hint}",
                category=ErrorCategory.INVALID_RESPONSE,
            )
        return str(content)

    @staticmethod
    def _log_usage(body: Dict[str, Any]) -> None:
        usage = body.get("usage") or {}
        if not usage:
            return
        details = usage.get("completion_tokens_details") or {}
        logger.info(
            "Groq usage: prompt=%s completion=%s%s total=%s",
            usage.get("prompt_tokens"), usage.get("completion_tokens"),
            f" (reasoning={details.get('reasoning_tokens')})"
            if details.get("reasoning_tokens") else "",
            usage.get("total_tokens"),
        )

    # -------------------------------------------------------------- errors
    def _error_for(self, response: httpx.Response) -> AppError:
        """Map a Groq error response onto a category. No retry decisions here."""
        status = response.status_code
        snippet = response.text[:500]
        error = _error_object(response)
        code = str(error.get("code") or "")
        message = str(error.get("message") or "").lower()

        # Error bodies can echo request content, so they stay out of INFO logs.
        logger.error("Groq API error %s%s", status, f" ({code})" if code else "")
        logger.debug("Groq error body: %s", snippet)

        if status == 400 and code == "json_validate_failed":
            generated = error.get("failed_generation")
            if isinstance(generated, str) and generated.strip():
                logger.warning("Groq flagged the JSON as invalid; handing the generated "
                               "text to the parser instead of spending another request.")
                return _SalvagedGeneration(generated)
            return self._fail(
                LLMInvalidResponseError, "Groq could not produce valid JSON for this request.",
                category=ErrorCategory.INVALID_RESPONSE, internal=snippet,
            )
        if status == 401:
            return self._fail(
                LLMNotConfiguredError,
                "Groq rejected the API key. Check GROQ_API_KEY in backend/.env.",
                category=ErrorCategory.AUTH, internal=snippet, http_status=status,
            )
        if status == 403:
            return self._fail(
                LLMNotConfiguredError,
                "This Groq key is not allowed to use the configured model. "
                "Check GROQ_MODEL and your Groq account.",
                category=ErrorCategory.AUTH, internal=snippet, http_status=status,
            )
        if status == 404 or code in {"model_not_found", "model_decommissioned"}:
            return self._fail(
                LLMRequestError,
                f"The Groq model '{self._model}' is not available. Check GROQ_MODEL in "
                "backend/.env.",
                category=ErrorCategory.MODEL_UNAVAILABLE, code="LLM_MODEL_NOT_FOUND",
                internal=snippet, http_status=status,
            )
        if status == 413 or code == "context_length_exceeded" or "request too large" in message:
            return self._fail(
                LLMRateLimitError,
                "This request is too large for your Groq plan's token limit. "
                "Lower LLM_CHUNK_CHAR_SIZE in backend/.env.",
                category=ErrorCategory.REQUEST_TOO_LARGE, code="LLM_REQUEST_TOO_LARGE",
                internal=snippet, http_status=status,
            )
        if status == 429:
            return self._fail(
                LLMRateLimitError,
                "Groq is rate limiting requests or the plan's quota is used up. "
                "Wait a little and try again.",
                category=ErrorCategory.RATE_LIMIT, internal=snippet, http_status=status,
            )
        if status >= 500 or status == 498:
            return self._fail(
                LLMRequestError, "Groq is temporarily unavailable. Try again in a moment.",
                category=ErrorCategory.SERVER_ERROR, status_code=503, code="LLM_UNAVAILABLE",
                internal=snippet, http_status=status,
            )
        return self._fail(
            LLMRequestError, "Groq rejected the request.",
            category=ErrorCategory.BAD_REQUEST, internal=snippet, http_status=status,
        )

    @staticmethod
    def _drop_rejected_parameter(payload: Dict[str, Any], exc: LLMRequestError) -> Optional[str]:
        """If Groq rejected an optional parameter, remove it and say which one."""
        if exc.details.get("status_code") != 400:
            return None
        text = (exc.internal or "").lower()
        for key in ("reasoning_effort", "response_format"):
            if key in payload and key in text:
                payload.pop(key, None)
                return key
        return None

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
        return error_cls(message, code=code, status_code=status_code,
                         details=details, internal=internal)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[float]:
        header = response.headers.get("retry-after")
        if not header:
            return None
        try:
            return max(0.0, float(header))
        except ValueError:
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Exponential backoff with jitter, so parallel retries do not sync up."""
        return min(MAX_BACKOFF_SECONDS, (2 ** (attempt - 1)) + random.uniform(0, 0.5))

    @staticmethod
    async def _sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)


class _SalvagedGeneration(LLMInvalidResponseError):
    """Internal signal: Groq refused its own JSON but returned the text.

    Raised from the transport and caught in :meth:`GroqClient.complete_json`,
    which returns ``text`` to the caller's parser. Never escapes this module.
    """

    def __init__(self, text: str) -> None:
        super().__init__("Groq returned JSON that it could not validate itself.")
        self.text = text


def _error_object(response: httpx.Response) -> Dict[str, Any]:
    """The ``error`` object of a Groq error body, or an empty dict."""
    try:
        body = response.json()
    except ValueError:
        return {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"]
    return {}
