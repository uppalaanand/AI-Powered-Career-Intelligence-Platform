"""Chat-completions provider for vendors that speak the OpenAI protocol.

Both xAI and Groq expose ``POST {base_url}/chat/completions`` with the same
request and response shape, so they share one implementation and differ only in
their credentials, model and base URL.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.ai.providers.base import HttpLLMProvider
from app.config import get_logger
from app.utils.errors import LLMRequestError

logger = get_logger(__name__)


class OpenAICompatibleProvider(HttpLLMProvider):
    """Shared implementation. Subclasses only supply configuration."""

    def __init__(
        self,
        *,
        api_key: Optional[str],
        model: str,
        base_url: str,
        timeout: float,
        max_attempts: int,
        temperature: float,
    ) -> None:
        super().__init__(timeout=timeout, max_attempts=max_attempts)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

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
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }

        try:
            body = await self._request(payload)
        except LLMRequestError as exc:
            # Some models reject the structured-output flag with a 400. Dropping
            # it costs one extra call only in that specific case; the prompt
            # already demands JSON-only output, so the result is still usable.
            if exc.details.get("status_code") == 400 and "response_format" in payload:
                logger.warning("%s rejected response_format; retrying without it.", self.label)
                payload.pop("response_format", None)
                body = await self._request(payload)
            else:
                raise

        return self._extract_content(body)

    async def _request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._post(
            f"{self._base_url}/chat/completions",
            payload=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    def _extract_content(self, body: Dict[str, Any]) -> str:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise self.invalid_response(
                f"The {self.label} response was missing the expected content.",
                internal=str(body)[:600],
            ) from exc

        if not content or not str(content).strip():
            raise self.invalid_response(f"{self.label} returned an empty response.")

        usage = body.get("usage") or {}
        if usage:
            logger.info(
                "%s usage: prompt=%s completion=%s total=%s",
                self.label, usage.get("prompt_tokens"),
                usage.get("completion_tokens"), usage.get("total_tokens"),
            )
        return str(content)
