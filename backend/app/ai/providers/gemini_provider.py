"""Google Gemini - first fallback.

Gemini does not speak the chat-completions protocol, so this is the one place
that needs a real adapter:

    system prompt  -> ``systemInstruction.parts[].text``
    user prompt    -> ``contents[0].parts[].text``
    JSON mode      -> ``generationConfig.responseMimeType = application/json``
    answer         -> ``candidates[0].content.parts[].text``

The *semantics* are identical to the other providers: same system prompt, same
user prompt, same JSON schema demanded of the model. Only the envelope differs.

Google also reports a bad key as ``400 API_KEY_INVALID`` rather than 401, so
:meth:`_error_for_status` looks inside the body before deciding the category -
otherwise a missing key would be reported as "request rejected" and the
operator would have no idea what to fix.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ai.providers.base import HttpLLMProvider, ProviderCategory
from app.config import get_logger, get_settings
from app.utils.errors import LLMNotConfiguredError, LLMRateLimitError, LLMRequestError

logger = get_logger(__name__)

# finishReason values that mean "there is no usable answer here".
_UNUSABLE_FINISH_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class GeminiProvider(HttpLLMProvider):
    name = "gemini"
    label = "Google Gemini"
    api_key_env = "GEMINI_API_KEY"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            timeout=settings.gemini_timeout_seconds,
            max_attempts=settings.llm_provider_max_attempts,
        )
        self._api_key = api_key or settings.gemini_api_key
        self._model = model or settings.gemini_model
        self._base_url = (base_url or settings.gemini_base_url).rstrip("/")
        self._temperature = settings.llm_temperature

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------------ call
    async def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4000,
        temperature: Optional[float] = None,
    ) -> str:
        if not self.is_configured:
            raise self.not_configured_error()

        payload: Dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": self._temperature if temperature is None else temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }

        body = await self._post(
            f"{self._base_url}/models/{self._model}:generateContent",
            payload=payload,
            # Header auth, not `?key=`: the key never lands in a URL, a log line
            # or an error snippet.
            headers={"x-goog-api-key": self._api_key or "", "Content-Type": "application/json"},
        )
        return self._extract_content(body)

    # ------------------------------------------------------------- internals
    def _extract_content(self, body: Dict[str, Any]) -> str:
        feedback = body.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise self.invalid_response(
                "Gemini blocked the request before answering "
                f"({feedback.get('blockReason')})."
            )

        candidates = body.get("candidates") or []
        if not candidates:
            raise self.invalid_response(
                "Gemini returned no candidates.", internal=str(body)[:600]
            )

        candidate = candidates[0]
        finish_reason = str(candidate.get("finishReason") or "").upper()
        if finish_reason in _UNUSABLE_FINISH_REASONS:
            raise self.invalid_response(f"Gemini stopped early ({finish_reason}).")

        parts: List[Dict[str, Any]] = (candidate.get("content") or {}).get("parts") or []
        text = "".join(str(part.get("text") or "") for part in parts)

        if not text.strip():
            # MAX_TOKENS with no text usually means the whole budget went to
            # thinking tokens; that is a provider failure, not a schema failure.
            reason = finish_reason or "no text in the response"
            raise self.invalid_response(f"Gemini returned an empty response ({reason}).")

        usage = body.get("usageMetadata") or {}
        if usage:
            logger.info(
                "Gemini usage: prompt=%s candidates=%s total=%s",
                usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
                usage.get("totalTokenCount"),
            )
        return text

    def _error_for_status(self, response):  # type: ignore[override]
        """Google-specific statuses first, then the shared mapping."""
        status = response.status_code
        snippet = response.text[:500]
        reason = self._google_reason(response)

        if status == 400 and reason in {"API_KEY_INVALID", "INVALID_ARGUMENT_API_KEY"}:
            logger.error("Gemini rejected the API key.")
            return self._fail(
                LLMNotConfiguredError,
                f"Gemini rejected the API key. Check {self.api_key_env} in backend/.env.",
                category=ProviderCategory.AUTH,
                internal=snippet,
                http_status=status,
            )
        if status == 403 and reason in {"SERVICE_DISABLED", "PERMISSION_DENIED", ""}:
            logger.error("Gemini denied access (%s).", reason or "PERMISSION_DENIED")
            return self._fail(
                LLMNotConfiguredError,
                "Gemini denied access to this key. Enable the Generative Language API "
                "for the project that owns GEMINI_API_KEY.",
                category=ProviderCategory.AUTH,
                internal=snippet,
                http_status=status,
            )
        if status == 429 or reason == "RESOURCE_EXHAUSTED":
            logger.error("Gemini quota exhausted.")
            return self._fail(
                LLMRateLimitError,
                "Gemini is rate limiting requests or the free-tier quota is exhausted.",
                category=ProviderCategory.RATE_LIMIT,
                internal=snippet,
                http_status=status,
            )
        if status == 404 or reason == "NOT_FOUND":
            logger.error("Gemini model not found.")
            return self._fail(
                LLMRequestError,
                f"The Gemini model '{self._model}' is not available to this key. "
                "Check GEMINI_MODEL in backend/.env.",
                category=ProviderCategory.MODEL_UNAVAILABLE,
                code="LLM_MODEL_NOT_FOUND",
                internal=snippet,
                http_status=status,
            )
        return super()._error_for_status(response)

    @staticmethod
    def _google_reason(response) -> str:
        """Pull ``error.status``/``error.details[].reason`` out of a Google error body.

        Written defensively: a proxy or gateway in front of the API can return
        an error body in any shape at all, and a crash while classifying a
        failure would break the fallback chain instead of advancing it.
        """
        try:
            body = response.json()
        except ValueError:
            return ""
        if not isinstance(body, dict):
            return ""
        error = body.get("error")
        if not isinstance(error, dict):
            return ""
        details = error.get("details")
        if isinstance(details, list):
            for detail in details:
                if isinstance(detail, dict) and detail.get("reason"):
                    return str(detail["reason"]).upper()
        return str(error.get("status") or "").upper()
