"""Multi-model fallback tests: Grok -> Gemini -> Groq.

Every provider here is a stub that records its calls. **No real API request is
made and no key is needed** - these tests exist precisely because the accounts
are on free tiers, and the orchestration logic is what needs proving, not the
vendors' HTTP.

What each stub counts is the number of times ``complete_json`` was invoked, so
the tests can assert the two rules that protect the quota:

* a provider that succeeds stops the chain - the rest are never called;
* a provider that fails is not retried before moving on.
"""

from __future__ import annotations

from typing import List, Optional

import httpx
import pytest

from app.ai.llm_orchestrator import LLMOrchestrator
from app.config import get_settings
from app.ai.providers import ProviderCategory
from app.ai.providers.base import LLMProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.grok_provider import GrokProvider
from app.ai.providers.groq_provider import GroqProvider
from app.utils.errors import (
    LLMAllProvidersFailedError,
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
)

VALID_JSON = """
{
  "summary": "The team discussed the mobile application launch.",
  "key_points": ["Launch is on track"],
  "decisions": ["Continue with the planned launch date"],
  "participants": ["Ravi", "Priya"],
  "action_items": [
    {"task": "Complete API integration", "assigned_to": "Ravi", "deadline": "Friday",
     "priority": "high", "status": "pending", "context": "Ravi agreed to finish it."}
  ]
}
"""

TRANSCRIPT = "Ravi will complete the API integration by Friday. Priya will test the UI."

#: Real environment variable names, so error messages under test match what an
#: operator would actually have to set.
ENV_VARS = {"grok": "XAI_API_KEY", "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}


@pytest.fixture
def no_provider_keys(monkeypatch):
    """Run with every provider key unset, whatever the developer's .env holds.

    Environment variables outrank the dotenv file in pydantic-settings, so
    blanking them here is enough once the settings cache is cleared.
    """
    for variable in ENV_VARS.values():
        monkeypatch.setenv(variable, "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class StubProvider(LLMProvider):
    """A provider that returns queued responses or raises a queued error."""

    def __init__(
        self,
        name: str,
        *,
        responses: Optional[List[str]] = None,
        error: Optional[Exception] = None,
        configured: bool = True,
        model: str = "stub-model",
    ) -> None:
        self.name = name
        self.label = name.title()
        self.api_key_env = ENV_VARS[name]
        self._responses = list(responses or [])
        self._error = error
        self._configured = configured
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def is_configured(self) -> bool:
        return self._configured

    async def complete_json(self, *, system_prompt: str, user_prompt: str, **_) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        if not self._responses:
            raise AssertionError(f"{self.name} ran out of scripted responses")
        return self._responses.pop(0)


def auth_error(provider: str) -> LLMNotConfiguredError:
    return LLMNotConfiguredError(
        f"{provider} rejected the API key.",
        details={"provider": provider, "category": ProviderCategory.AUTH},
    )


def rate_limit_error(provider: str) -> LLMRateLimitError:
    return LLMRateLimitError(
        f"{provider} is rate limited.",
        details={"provider": provider, "category": ProviderCategory.RATE_LIMIT},
    )


def timeout_error(provider: str) -> LLMRequestError:
    return LLMRequestError(
        f"{provider} timed out.",
        details={"provider": provider, "category": ProviderCategory.TIMEOUT},
    )


def chain(grok: StubProvider, gemini: StubProvider, groq: StubProvider) -> LLMOrchestrator:
    """Orchestrator over the three stubs, with corrective re-prompts disabled.

    ``schema_retry_attempts=1`` keeps the call counts in these tests equal to
    the number of provider attempts, which is exactly what is being asserted.
    """
    return LLMOrchestrator([grok, gemini, groq], schema_retry_attempts=1)


# ---------------------------------------------------------------- the chain
@pytest.mark.asyncio
class TestFallbackChain:
    async def test_1_grok_succeeds_and_nothing_else_is_called(self):
        grok = StubProvider("grok", responses=[VALID_JSON])
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        result, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert result.summary.startswith("The team discussed")
        assert metadata["provider"] == "grok"
        assert metadata["fallback_used"] is False
        assert (grok.calls, gemini.calls, groq.calls) == (1, 0, 0)

    async def test_2_grok_fails_then_gemini_succeeds_and_groq_is_untouched(self):
        grok = StubProvider("grok", error=timeout_error("grok"))
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        result, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert result.summary
        assert metadata["provider"] == "gemini"
        assert metadata["fallback_used"] is True
        assert [entry["provider"] for entry in metadata["providers_failed"]] == ["grok"]
        assert (grok.calls, gemini.calls, groq.calls) == (1, 1, 0)

    async def test_3_only_groq_succeeds(self):
        grok = StubProvider("grok", error=auth_error("grok"))
        gemini = StubProvider("gemini", error=rate_limit_error("gemini"))
        groq = StubProvider("groq", responses=[VALID_JSON])

        result, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert result.action_items[0].task == "Complete API integration"
        assert metadata["provider"] == "groq"
        assert (grok.calls, gemini.calls, groq.calls) == (1, 1, 1)

    async def test_4_all_fail_is_a_controlled_error_not_a_crash(self):
        grok = StubProvider("grok", error=auth_error("grok"))
        gemini = StubProvider("gemini", error=rate_limit_error("gemini"))
        groq = StubProvider("groq", error=timeout_error("groq"))

        with pytest.raises(LLMAllProvidersFailedError) as exc:
            await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert exc.value.code == "LLM_ALL_PROVIDERS_FAILED"
        assert exc.value.status_code == 502
        assert "could not be completed" in exc.value.message
        # The message tells the operator what happened without leaking secrets.
        assert "authentication rejected" in exc.value.message
        assert "rate limited" in exc.value.message
        assert (grok.calls, gemini.calls, groq.calls) == (1, 1, 1)

    async def test_5_authentication_error_moves_on_without_retrying_grok(self):
        grok = StubProvider("grok", error=auth_error("grok"))
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        _, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert grok.calls == 1, "an auth failure must not be retried"
        assert metadata["provider"] == "gemini"
        assert metadata["providers_failed"][0]["category"] == ProviderCategory.AUTH

    async def test_6_rate_limited_provider_is_not_called_again(self):
        grok = StubProvider("grok", error=rate_limit_error("grok"))
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        _, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert grok.calls == 1, "a 429 must not consume more quota"
        assert metadata["providers_failed"][0]["category"] == ProviderCategory.RATE_LIMIT
        assert groq.calls == 0

    async def test_7_invalid_grok_output_falls_through_to_gemini(self):
        grok = StubProvider("grok", responses=["I'm afraid I can't help with that."])
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        result, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert metadata["provider"] == "gemini"
        assert result.summary.startswith("The team discussed")
        assert metadata["providers_failed"][0]["category"] == ProviderCategory.INVALID_RESPONSE
        assert groq.calls == 0

    async def test_7b_schema_violating_output_is_never_stored(self):
        """A reply that parses but does not validate is a provider failure."""
        grok = StubProvider("grok", responses=['{"key_points": ["no summary field"]}'])
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        result, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert metadata["provider"] == "gemini"
        assert result.summary  # the valid Gemini answer, not the broken Grok one

    async def test_8_unconfigured_gemini_is_skipped_entirely(self):
        grok = StubProvider("grok", error=auth_error("grok"))
        gemini = StubProvider("gemini", configured=False)
        groq = StubProvider("groq", responses=[VALID_JSON])

        _, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert gemini.calls == 0, "a provider with no key must never be called"
        assert metadata["provider"] == "groq"
        assert metadata["providers_skipped"] == ["gemini"]

    async def test_9_no_keys_at_all_is_a_configuration_error_with_no_requests(self):
        grok = StubProvider("grok", configured=False)
        gemini = StubProvider("gemini", configured=False)
        groq = StubProvider("groq", configured=False)

        with pytest.raises(LLMNotConfiguredError) as exc:
            await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert exc.value.code == "LLM_NOT_CONFIGURED"
        assert "XAI_API_KEY" in exc.value.message
        assert (grok.calls, gemini.calls, groq.calls) == (0, 0, 0)

    async def test_only_grok_configured_and_failing_names_the_missing_providers(self):
        grok = StubProvider("grok", error=rate_limit_error("grok"))
        gemini = StubProvider("gemini", configured=False)
        groq = StubProvider("groq", configured=False)

        with pytest.raises(LLMAllProvidersFailedError) as exc:
            await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert "gemini" in exc.value.message and "groq" in exc.value.message
        assert exc.value.details["skipped"] == ["gemini", "groq"]

    async def test_a_provider_raising_an_unexpected_error_does_not_end_the_chain(self):
        grok = StubProvider("grok", error=RuntimeError("boom"))
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        _, metadata = await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert metadata["provider"] == "gemini"
        assert groq.calls == 0

    async def test_empty_transcript_is_rejected_before_any_provider_is_called(self):
        grok = StubProvider("grok", responses=[VALID_JSON])
        gemini = StubProvider("gemini", responses=[VALID_JSON])
        groq = StubProvider("groq", responses=[VALID_JSON])

        with pytest.raises(LLMInvalidResponseError):
            await chain(grok, gemini, groq).analyze_transcript("   ")

        assert (grok.calls, gemini.calls, groq.calls) == (0, 0, 0)


# ------------------------------------------------------- quota-conscious ops
@pytest.mark.asyncio
class TestQuotaDiscipline:
    async def test_a_dead_provider_stops_burning_calls_on_a_chunked_transcript(self):
        """A long transcript must not spend one dead-provider call per chunk."""
        long_text = "The team discussed the launch in detail. " * 800
        grok = StubProvider("grok", error=auth_error("grok"))
        gemini = StubProvider("gemini", responses=[VALID_JSON] * 40)
        groq = StubProvider("groq", responses=[VALID_JSON])

        _, metadata = await chain(grok, gemini, groq).analyze_transcript(long_text)

        assert metadata["provider"] == "gemini"
        assert metadata["chunk_count"] > 1
        # Concurrency is 3, so at most the first in-flight batch reaches a
        # provider that is already known to be broken - never one call per chunk.
        assert grok.calls <= 3
        assert groq.calls == 0

    async def test_providers_are_called_strictly_in_order(self):
        order: List[str] = []

        class RecordingProvider(StubProvider):
            async def complete_json(self, **kwargs):
                order.append(self.name)
                return await super().complete_json(**kwargs)

        grok = RecordingProvider("grok", error=timeout_error("grok"))
        gemini = RecordingProvider("gemini", error=timeout_error("gemini"))
        groq = RecordingProvider("groq", responses=[VALID_JSON])

        await chain(grok, gemini, groq).analyze_transcript(TRANSCRIPT)

        assert order == ["grok", "gemini", "groq"]

    async def test_configured_providers_are_reported_without_calling_anything(self):
        grok = StubProvider("grok", responses=[VALID_JSON])
        gemini = StubProvider("gemini", configured=False)
        groq = StubProvider("groq", responses=[VALID_JSON])
        orchestrator = chain(grok, gemini, groq)

        assert [p.name for p in orchestrator.configured_providers] == ["grok", "groq"]
        assert orchestrator.is_configured is True
        assert (grok.calls, gemini.calls, groq.calls) == (0, 0, 0)


# -------------------------------------------------- provider error mapping
class TestProviderErrorClassification:
    """Each provider must turn its vendor's HTTP status into the right category.

    Responses are mocked with httpx - no network, no credentials.
    """

    @staticmethod
    def _classify(provider, status: int, body: str = "{}"):
        response = httpx.Response(
            status_code=status,
            text=body,
            request=httpx.Request("POST", "https://example.test"),
        )
        return provider._error_for_status(response)

    @pytest.mark.parametrize(
        "status,expected",
        [
            (401, ProviderCategory.AUTH),
            (403, ProviderCategory.AUTH),
            (404, ProviderCategory.MODEL_UNAVAILABLE),
            (429, ProviderCategory.RATE_LIMIT),
            (500, ProviderCategory.SERVER_ERROR),
            (503, ProviderCategory.SERVER_ERROR),
            (400, ProviderCategory.BAD_REQUEST),
        ],
    )
    def test_openai_compatible_statuses(self, status, expected):
        for provider in (GrokProvider(api_key="test"), GroqProvider(api_key="test")):
            error = self._classify(provider, status)
            assert error.details["category"] == expected

    def test_gemini_reports_a_bad_key_as_auth_not_bad_request(self):
        """Google answers 400, not 401, when the key is wrong."""
        body = '{"error": {"code": 400, "status": "INVALID_ARGUMENT", ' \
               '"details": [{"reason": "API_KEY_INVALID"}]}}'
        error = self._classify(GeminiProvider(api_key="test"), 400, body)
        assert error.details["category"] == ProviderCategory.AUTH
        assert "GEMINI_API_KEY" in error.message

    def test_gemini_quota_exhaustion_is_a_rate_limit(self):
        body = '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}'
        error = self._classify(GeminiProvider(api_key="test"), 429, body)
        assert error.details["category"] == ProviderCategory.RATE_LIMIT

    def test_gemini_missing_model_names_the_setting_to_fix(self):
        error = self._classify(GeminiProvider(api_key="test"), 404)
        assert error.details["category"] == ProviderCategory.MODEL_UNAVAILABLE
        assert "GEMINI_MODEL" in error.message

    def test_error_messages_never_contain_the_api_key(self):
        secret = "sk-super-secret-value"
        for provider in (
            GrokProvider(api_key=secret),
            GroqProvider(api_key=secret),
            GeminiProvider(api_key=secret),
        ):
            error = self._classify(provider, 401, '{"error": "unauthorized"}')
            assert secret not in error.message
            assert secret not in str(error.details)
            assert secret not in (error.internal or "")


@pytest.mark.usefixtures("no_provider_keys")
class TestProviderConfiguration:
    def test_a_provider_without_a_key_reports_itself_unconfigured(self):
        assert GeminiProvider().is_configured is False
        assert GroqProvider().is_configured is False
        assert GrokProvider().is_configured is False

    def test_a_provider_with_a_key_is_configured(self):
        assert GeminiProvider(api_key="test").is_configured is True

    def test_settings_report_the_chain_from_configuration_alone(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.configured_llm_providers == ["gemini"]
        assert settings.llm_configured is True
        assert settings.grok_configured is False

    @pytest.mark.asyncio
    async def test_calling_an_unconfigured_provider_raises_before_any_request(self):
        for provider in (GrokProvider(), GeminiProvider(), GroqProvider()):
            with pytest.raises(LLMNotConfiguredError) as exc:
                await provider.complete_json(system_prompt="s", user_prompt="u")
            assert exc.value.details["category"] == ProviderCategory.NOT_CONFIGURED
            assert provider.api_key_env in exc.value.message

    @pytest.mark.asyncio
    async def test_health_check_on_an_unconfigured_provider_makes_no_call(self):
        report = await GeminiProvider().check_connection()
        assert report["configured"] is False
        assert report["reachable"] is False
        assert "GEMINI_API_KEY" in report["message"]
