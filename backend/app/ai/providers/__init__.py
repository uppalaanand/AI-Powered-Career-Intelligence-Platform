"""LLM providers.

Three vendors behind one interface, so nothing above this package knows which
one answered:

    LLMProvider (contract)
        |- GrokProvider   xAI          primary
        |- GeminiProvider Google       fallback 1
        |- GroqProvider   Groq         fallback 2

Fallback order and selection live in ``app.ai.llm_orchestrator``.
"""

from app.ai.providers.base import (
    CATEGORY_LABELS,
    LLMProvider,
    ProviderCategory,
    category_of,
    describe,
)
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.grok_provider import GrokProvider
from app.ai.providers.groq_provider import GroqProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

#: Name -> class. ``LLM_PROVIDER_ORDER`` is resolved through this registry.
PROVIDER_REGISTRY = {
    GrokProvider.name: GrokProvider,
    GeminiProvider.name: GeminiProvider,
    GroqProvider.name: GroqProvider,
}

__all__ = [
    "CATEGORY_LABELS",
    "GeminiProvider",
    "GrokProvider",
    "GroqProvider",
    "LLMProvider",
    "OpenAICompatibleProvider",
    "PROVIDER_REGISTRY",
    "ProviderCategory",
    "category_of",
    "describe",
]
