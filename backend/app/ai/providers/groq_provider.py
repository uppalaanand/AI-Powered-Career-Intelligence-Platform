"""Groq - second fallback.

Groq's OpenAI-compatible endpoint lives at ``https://api.groq.com/openai/v1``
and supports ``response_format: json_object`` on the instruct models, so it
reuses the shared chat-completions implementation unchanged.
"""

from __future__ import annotations

from typing import Optional

from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.config import get_settings


class GroqProvider(OpenAICompatibleProvider):
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
        super().__init__(
            api_key=api_key or settings.groq_api_key,
            model=model or settings.groq_model,
            base_url=base_url or settings.groq_base_url,
            timeout=settings.groq_timeout_seconds,
            max_attempts=settings.llm_provider_max_attempts,
            temperature=settings.llm_temperature,
        )
