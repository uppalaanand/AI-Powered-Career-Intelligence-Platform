"""Backwards-compatible alias for the Grok client.

The xAI client moved to ``app.ai.providers.grok_provider`` when Gemini and Groq
joined it behind a shared interface. This module stays so existing imports of
``GrokClient`` keep working; new code should import ``GrokProvider`` - or, in
almost every case, use ``app.ai.llm_orchestrator.LLMOrchestrator`` and let it
pick the provider.
"""

from __future__ import annotations

from app.ai.providers.grok_provider import GrokProvider

#: Historical name for :class:`~app.ai.providers.grok_provider.GrokProvider`.
GrokClient = GrokProvider

__all__ = ["GrokClient", "GrokProvider"]
