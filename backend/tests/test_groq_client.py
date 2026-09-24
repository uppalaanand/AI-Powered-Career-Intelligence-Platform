"""Groq-only LLM tests.

Groq is the application's only LLM provider. These tests prove three things:

1. **It really is the only one.** No Grok/xAI or Gemini module exists, no
   runtime code points at their APIs, and every LLM path defaults to Groq.
2. **The client speaks Groq correctly** - request shape, JSON mode, reasoning
   effort, response parsing - checked against mocked HTTP with ``respx``.
3. **It spends quota carefully.** Each test counts the HTTP requests actually
   made: bad keys, unknown models and oversized requests are never retried,
   rate limits are retried at most once and only when Groq says the wait is
   short, and a normal analysis costs exactly one request.

No test makes a real network request or needs GROQ_API_KEY.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from typing import Any, Dict, List

import httpx
import pytest
import respx

from app.ai.groq_client import ErrorCategory, GroqClient, category_of
from app.ai.llm_service import LLMService
from app.utils.errors import (
    LLMInvalidResponseError,
    LLMNotConfiguredError,
    LLMRateLimitError,
    LLMRequestError,
)
from tests.helpers import StubGroqClient, auth_error, rate_limit_error

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"

VALID_ANALYSIS = json.dumps({
    "summary": "The team planned the mobile application launch.",
    "key_points": ["Launch is on track"],
    "decisions": ["Release by Friday"],
    "participants": ["Ravi", "Priya"],
    "action_items": [{"task": "Finish the API integration", "assigned_to": "Ravi",
                      "deadline": "Friday", "priority": "high", "status": "pending"}],
})


def completion(content: str, *, finish_reason: str = "stop") -> Dict[str, Any]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def groq_error(status: int, code: str = "", message: str = "error",
               **extra: Any) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "code": code, **extra}})


@pytest.fixture
def client(monkeypatch) -> GroqClient:
    """A Groq client with a test key and no real waiting between retries."""
    instance = GroqClient(api_key="test-groq-key", model="openai/gpt-oss-20b")
    instance._max_attempts = 2
    instance._max_rate_limit_wait = 20.0
    instance._reasoning_effort = "low"
    instance.waits: List[float] = []

    async def no_sleep(seconds: float) -> None:
        instance.waits.append(seconds)

    monkeypatch.setattr(instance, "_sleep", no_sleep)
    return instance


# ======================================================= Groq is the only LLM
class TestGroqIsTheOnlyProvider:
    def test_the_old_provider_modules_are_gone(self):
        for module in ("app.ai.providers", "app.ai.llm_orchestrator", "app.ai.llm_client",
                       "app.knowledge.embeddings.gemini"):
            assert importlib.util.find_spec(module) is None, f"{module} should not exist"

    def test_no_runtime_code_calls_xai_or_gemini(self):
        forbidden = ("api.x.ai", "generativelanguage.googleapis.com", "XAI_API_KEY",
                     "GEMINI_API_KEY", "GrokProvider", "GeminiProvider", "LLMOrchestrator")
        offenders = []
        for path in APP_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            offenders += [f"{path.name}: {needle}" for needle in forbidden if needle in text]
        assert offenders == []

    def test_every_llm_path_defaults_to_groq(self):
        service = LLMService()
        assert isinstance(service._client, GroqClient)
        assert service.provider_name == "groq"

    def test_rag_and_analysis_share_the_same_llm_service(self):
        from app.services.intelligence_service import IntelligenceService
        from app.services.rag_service import RAGService

        assert isinstance(IntelligenceService(meetings=object(), repository=object())._llm,
                          LLMService)
        assert isinstance(RAGService(search=object())._llm, LLMService)

    def test_settings_expose_no_other_llm_provider(self):
        from app.config import get_settings

        fields = set(type(get_settings()).model_fields)
        assert not {f for f in fields if f.startswith(("xai_", "gemini_"))}
        assert "llm_provider_order" not in fields
        assert {"groq_api_key", "groq_model"} <= fields


# ============================================================ request shape
@pytest.mark.asyncio
class TestRequestShape:
    async def test_json_mode_request_goes_to_groq(self, client):
        seen: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json=completion('{"ok": true}'))

        with respx.mock:
            respx.post(GROQ_URL).mock(side_effect=capture)
            raw = await client.complete_json(system_prompt="sys", user_prompt="user",
                                             max_tokens=321)

        assert raw == '{"ok": true}'
        assert seen["url"] == GROQ_URL
        assert seen["auth"] == "Bearer test-groq-key"
        body = seen["body"]
        assert body["model"] == "openai/gpt-oss-20b"
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_completion_tokens"] == 321
        assert [m["role"] for m in body["messages"]] == ["system", "user"]

    async def test_reasoning_models_are_asked_for_low_effort(self, client):
        seen: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=completion("{}"))

        with respx.mock:
            respx.post(GROQ_URL).mock(side_effect=capture)
            await client.complete_json(system_prompt="s", user_prompt="u")

        assert seen["reasoning_effort"] == "low"

    async def test_non_reasoning_models_never_get_reasoning_effort(self, client):
        client._model = "llama-3.3-70b-versatile"
        seen: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=completion("{}"))

        with respx.mock:
            respx.post(GROQ_URL).mock(side_effect=capture)
            await client.complete_json(system_prompt="s", user_prompt="u")

        assert "reasoning_effort" not in seen

    async def test_no_request_is_made_without_a_key(self):
        keyless = GroqClient(api_key=None)
        keyless._api_key = None
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(GROQ_URL)
            with pytest.raises(LLMNotConfiguredError) as exc:
                await keyless.complete_json(system_prompt="s", user_prompt="u")
        assert route.call_count == 0
        assert "GROQ_API_KEY" in exc.value.message
        assert category_of(exc.value) == ErrorCategory.NOT_CONFIGURED


# ================================================== errors, retries, quota
@pytest.mark.asyncio
class TestErrorHandlingAndQuota:
    async def _run(self, client, *responses):
        with respx.mock:
            route = respx.post(GROQ_URL).mock(side_effect=list(responses))
            try:
                result = await client.complete_json(system_prompt="s", user_prompt="u")
            except Exception as exc:  # noqa: BLE001
                return exc, route.call_count
            return result, route.call_count

    async def test_bad_key_is_reported_and_never_retried(self, client):
        error, calls = await self._run(client, groq_error(401, "invalid_api_key"))
        assert isinstance(error, LLMNotConfiguredError)
        assert category_of(error) == ErrorCategory.AUTH
        assert "GROQ_API_KEY" in error.message
        assert calls == 1

    async def test_forbidden_is_an_auth_error(self, client):
        error, calls = await self._run(client, groq_error(403))
        assert category_of(error) == ErrorCategory.AUTH and calls == 1

    async def test_unknown_model_names_the_setting_and_is_not_retried(self, client):
        error, calls = await self._run(client, groq_error(404, "model_not_found"))
        assert category_of(error) == ErrorCategory.MODEL_UNAVAILABLE
        assert "GROQ_MODEL" in error.message
        assert calls == 1

    async def test_request_too_large_is_not_retried(self, client):
        error, calls = await self._run(
            client, groq_error(413, "rate_limit_exceeded", "Request too large for model")
        )
        assert category_of(error) == ErrorCategory.REQUEST_TOO_LARGE
        assert "LLM_CHUNK_CHAR_SIZE" in error.message
        assert calls == 1

    async def test_rate_limit_with_a_long_wait_fails_fast(self, client):
        long_wait = httpx.Response(429, headers={"retry-after": "3600"},
                                   json={"error": {"message": "daily limit"}})
        error, calls = await self._run(client, long_wait)
        assert isinstance(error, LLMRateLimitError)
        assert category_of(error) == ErrorCategory.RATE_LIMIT
        assert calls == 1, "an exhausted daily quota must not be retried"
        assert client.waits == []

    async def test_rate_limit_with_a_short_wait_is_retried_once(self, client):
        short_wait = httpx.Response(429, headers={"retry-after": "2"},
                                    json={"error": {"message": "tpm"}})
        result, calls = await self._run(
            client, short_wait, httpx.Response(200, json=completion('{"ok": true}'))
        )
        assert result == '{"ok": true}'
        assert calls == 2
        assert client.waits == [2.0], "waits exactly as long as Groq asked"

    async def test_rate_limit_is_retried_at_most_once(self, client):
        client._max_attempts = 5
        short_wait = httpx.Response(429, headers={"retry-after": "1"},
                                    json={"error": {"message": "tpm"}})
        error, calls = await self._run(client, short_wait, short_wait, short_wait)
        assert isinstance(error, LLMRateLimitError)
        assert calls == 2

    async def test_server_errors_get_one_retry(self, client):
        result, calls = await self._run(
            client, httpx.Response(503), httpx.Response(200, json=completion("{}"))
        )
        assert result == "{}" and calls == 2

    async def test_persistent_server_errors_stop_after_the_attempt_limit(self, client):
        error, calls = await self._run(client, httpx.Response(500), httpx.Response(500))
        assert category_of(error) == ErrorCategory.SERVER_ERROR
        assert calls == 2

    async def test_timeouts_are_retried_then_reported(self, client):
        with respx.mock:
            route = respx.post(GROQ_URL).mock(side_effect=httpx.ReadTimeout("slow"))
            with pytest.raises(LLMRequestError) as exc:
                await client.complete_json(system_prompt="s", user_prompt="u")
        assert category_of(exc.value) == ErrorCategory.TIMEOUT
        assert route.call_count == 2

    async def test_invalid_json_from_groq_is_salvaged_without_another_request(self, client):
        failed = groq_error(400, "json_validate_failed", "bad json",
                            failed_generation='{"summary": "ok",}')
        result, calls = await self._run(client, failed)
        assert result == '{"summary": "ok",}', "the text goes to the repairing parser"
        assert calls == 1

    async def test_a_rejected_optional_parameter_is_dropped_once(self, client):
        payloads: List[Dict[str, Any]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payloads.append(json.loads(request.content))
            if len(payloads) == 1:
                return groq_error(400, "", "`reasoning_effort` is not supported with this model")
            return httpx.Response(200, json=completion("{}"))

        with respx.mock:
            respx.post(GROQ_URL).mock(side_effect=respond)
            await client.complete_json(system_prompt="s", user_prompt="u")

        assert "reasoning_effort" in payloads[0]
        assert "reasoning_effort" not in payloads[1]
        assert len(payloads) == 2

    async def test_other_bad_requests_are_not_retried(self, client):
        error, calls = await self._run(client, groq_error(400, "", "messages must not be empty"))
        assert category_of(error) == ErrorCategory.BAD_REQUEST and calls == 1

    async def test_an_empty_answer_explains_a_spent_token_budget(self, client):
        error, calls = await self._run(
            client, httpx.Response(200, json=completion("", finish_reason="length"))
        )
        assert isinstance(error, LLMInvalidResponseError)
        assert "token budget" in error.message

    async def test_a_malformed_body_is_a_controlled_error(self, client):
        error, _ = await self._run(client, httpx.Response(200, json={"unexpected": True}))
        assert isinstance(error, LLMInvalidResponseError)

    async def test_the_api_key_never_appears_in_an_error(self, client):
        client._api_key = "gsk-super-secret-value"
        for response in (groq_error(401), groq_error(404), groq_error(500), groq_error(429)):
            error, _ = await self._run(client, response, response)
            blob = f"{error.message} {error.details} {error.internal or ''}"
            assert "gsk-super-secret-value" not in blob

    async def test_health_check_without_a_key_makes_no_request(self):
        keyless = GroqClient(api_key=None)
        keyless._api_key = None
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(GROQ_URL)
            report = await keyless.check_connection()
        assert report["configured"] is False and route.call_count == 0


# ==================================================== LLMService over Groq
@pytest.mark.asyncio
class TestLLMServiceRequestBudget:
    async def test_a_normal_transcript_costs_exactly_one_request(self):
        """Summary, key points, decisions, participants and action items come
        back from ONE request - nothing is fetched field by field."""
        groq = StubGroqClient([VALID_ANALYSIS])
        result, metadata = await LLMService(groq).analyze_transcript(
            "Ravi will finish the API integration by Friday. Priya will test the UI."
        )
        assert groq.calls == 1
        assert result.summary and result.key_points and result.decisions
        assert result.participants and result.action_items
        assert result.action_items[0].deadline == "Friday"
        assert metadata["provider"] == "groq"

    async def test_malformed_output_gets_one_correction_then_stops(self):
        groq = StubGroqClient(["not json", "still not json"])
        with pytest.raises(LLMInvalidResponseError):
            await LLMService(groq, schema_retry_attempts=2).analyze_transcript("Some text.")
        assert groq.calls == 2

    async def test_strict_minimum_mode_never_re_prompts(self):
        groq = StubGroqClient(["not json"])
        with pytest.raises(LLMInvalidResponseError):
            await LLMService(groq, schema_retry_attempts=1).analyze_transcript("Some text.")
        assert groq.calls == 1

    async def test_a_dead_key_stops_a_long_transcript_after_one_request(self):
        """No fallback exists, so a bad key must not be tried once per chunk."""
        groq = StubGroqClient(error=auth_error())
        service = LLMService(groq)
        service._chunk_concurrency = 1
        long_text = "The team discussed the launch in detail. " * 800
        with pytest.raises(LLMNotConfiguredError):
            await service.analyze_transcript(long_text)
        assert groq.calls == 1

    async def test_quota_exhaustion_mid_meeting_keeps_the_finished_sections(self):
        class QuotaRunsOut(StubGroqClient):
            async def complete_json(self, **kwargs):
                if self.calls >= 2:
                    self.calls += 1
                    raise rate_limit_error()
                return await super().complete_json(**kwargs)

        groq = QuotaRunsOut([VALID_ANALYSIS, VALID_ANALYSIS])
        service = LLMService(groq)
        service._chunk_concurrency = 1
        long_text = "The team discussed the launch in detail. " * 800
        result, metadata = await service.analyze_transcript(long_text)
        assert metadata["partial"] is True
        assert metadata["degraded_reason"] == ErrorCategory.RATE_LIMIT
        assert result.summary
        assert groq.calls == 3, "stops at the first rate limit instead of trying every chunk"

    async def test_an_oversized_merge_is_done_locally_without_a_request(self, monkeypatch):
        groq = StubGroqClient([VALID_ANALYSIS] * 10)
        service = LLMService(groq)
        service._max_request_tokens = 10   # force the merge prompt over budget
        long_text = "The team discussed the launch in detail. " * 800
        result, metadata = await service.analyze_transcript(long_text)
        assert groq.calls == metadata["chunk_count"], "one request per chunk, none for the merge"
        assert result.summary

    async def test_generate_validated_returns_the_callers_model(self):
        from app.schemas.rag import RAGAnswer

        groq = StubGroqClient(['{"answer": "Friday.", "answer_found": true}'])
        answer, metadata = await LLMService(groq).generate_validated(
            system_prompt="s", user_prompt="u", validate=RAGAnswer.model_validate,
            max_tokens=100,
        )
        assert isinstance(answer, RAGAnswer) and answer.answer == "Friday."
        assert metadata == {"provider": "groq", "provider_label": "Groq", "model": "stub-model"}
