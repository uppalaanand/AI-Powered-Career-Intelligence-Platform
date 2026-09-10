"""xAI Grok - the primary provider.

xAI speaks the OpenAI chat-completions protocol, so the wire work lives in
:class:`~app.ai.providers.openai_compatible.OpenAICompatibleProvider` and this
file is configuration only.
"""

from __future__ import annotations

from typing import Optional

from app.ai.providers.openai_compatible import OpenAICompatibleProvider
from app.config import get_settings


class GrokProvider(OpenAICompatibleProvider):
    name = "grok"
    label = "Grok (xAI)"
    api_key_env = "XAI_API_KEY"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            api_key=api_key or settings.xai_api_key,
            model=model or settings.xai_model,
            base_url=base_url or settings.xai_base_url,
            timeout=settings.xai_timeout_seconds,
            # XAI_MAX_RETRIES predates the fallback chain; the shared cap wins so
            # a legacy `.env` cannot spend three calls on one dead provider.
            max_attempts=min(settings.xai_max_retries, settings.llm_provider_max_attempts),
            temperature=settings.xai_temperature,
        )
