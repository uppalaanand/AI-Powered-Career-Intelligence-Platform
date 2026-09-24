"""Shared test doubles.

Nothing in here makes a network request or needs a key:

* ``StubGroqClient``         - stands in for ``GroqClient``; scripted replies or
                               errors, and a call counter so tests can prove how
                               many LLM requests an operation cost.
* ``BagOfWordsModel``         - a deterministic stand-in for the local embedding
                               model. Texts that share words get closer vectors,
                               so retrieval tests are meaningful without
                               downloading the real model.
* ``fake_embedding_provider`` - the *real* ``FastEmbedProvider`` wired to that
                               model, so the provider's own code (threading,
                               validation, dimension checks) is still exercised.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional

from app.ai.groq_client import ErrorCategory
from app.config import get_settings
from app.knowledge.embeddings.fastembed_provider import FastEmbedProvider
from app.utils.errors import LLMNotConfiguredError, LLMRateLimitError, LLMRequestError


class StubGroqClient:
    """Duck-types ``GroqClient``: queued responses, or one error for every call."""

    name = "groq"
    label = "Groq"
    api_key_env = "GROQ_API_KEY"

    def __init__(
        self,
        responses: Optional[List[str]] = None,
        *,
        error: Optional[Exception] = None,
        configured: bool = True,
        model: str = "stub-model",
    ) -> None:
        self._responses = list(responses or [])
        self._error = error
        self._configured = configured
        self.model = model
        self.calls = 0
        self.prompts: List[str] = []
        self.system_prompts: List[str] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def complete_json(self, *, system_prompt: str, user_prompt: str, **_: Any) -> str:
        self.calls += 1
        self.system_prompts.append(system_prompt)
        self.prompts.append(user_prompt)
        if not self._configured:
            raise LLMNotConfiguredError(details={"category": ErrorCategory.NOT_CONFIGURED})
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError("StubGroqClient ran out of scripted responses")
        return self._responses.pop(0)

    async def check_connection(self) -> dict:
        return {"provider": "groq", "configured": self._configured, "reachable": True}


def auth_error() -> LLMNotConfiguredError:
    return LLMNotConfiguredError(
        "Groq rejected the API key.",
        details={"provider": "groq", "category": ErrorCategory.AUTH},
    )


def rate_limit_error() -> LLMRateLimitError:
    return LLMRateLimitError(
        "Groq is rate limiting requests.",
        details={"provider": "groq", "category": ErrorCategory.RATE_LIMIT},
    )


def timeout_error() -> LLMRequestError:
    return LLMRequestError(
        "Groq did not respond in time.",
        details={"provider": "groq", "category": ErrorCategory.TIMEOUT},
    )


_TOKEN = re.compile(r"[a-z]+")


class BagOfWordsModel:
    """Deterministic embedding model with fastembed's ``embed`` interface."""

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.embed_calls = 0
        self.texts_embedded = 0

    def embed(self, texts: Iterable[str], batch_size: int = 32) -> Iterable[List[float]]:
        texts = list(texts)
        self.embed_calls += 1
        self.texts_embedded += len(texts)
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in _TOKEN.findall(text.lower()):
                vector[_stable_hash(token) % self.dimensions] += 1.0
            if not any(vector):
                vector[0] = 1.0
            yield vector


def _stable_hash(token: str) -> int:
    """Python's hash() is salted per process; tests need the same value every run."""
    value = 0
    for character in token:
        value = (value * 131 + ord(character)) % 2_147_483_647
    return value


def fake_embedding_provider(
    dimensions: Optional[int] = None, model: Optional[BagOfWordsModel] = None
) -> FastEmbedProvider:
    """The real FastEmbedProvider, with the model swapped for BagOfWordsModel."""
    size = dimensions or get_settings().embedding_dimensions
    instance = model or BagOfWordsModel(size)
    return FastEmbedProvider(
        model="test/bag-of-words",
        dimensions=size,
        model_factory=lambda *_: instance,
    )
