"""Milestone 3, Task 5 - grounded question answering with Groq.

Retrieval is a fixed stub and Groq is ``StubGroqClient``, so **no embedding,
vector or LLM request is made**. What is real: the RAG service, the prompt, the
context builder, ``LLMService``'s validation loop and the answer schema.

The behaviours that matter most are the ones about *not* making things up:

* an answer is built only from retrieved context;
* when retrieval finds nothing, Groq is never called and the system says so;
* a meeting the model cites but was never shown is discarded;
* a malformed answer is corrected once, and otherwise rejected;
* when Groq fails, the API says so rather than inventing prose.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

import pytest

from app.ai.llm_service import LLMService
from app.ai.prompts.rag_prompt import NO_CONTEXT_ANSWER, SYSTEM_PROMPT, build_answer_prompt
from app.schemas.rag import RAGAnswer
from app.services.rag_service import RAGService
from app.services.semantic_search_service import RetrievedPassage
from app.utils.errors import LLMNotConfiguredError
from tests.helpers import StubGroqClient, auth_error, rate_limit_error

MEETING_A = "aaaaaaaa-1111-2222-3333-444444444444"
MEETING_B = "bbbbbbbb-1111-2222-3333-444444444444"


def passage(
    meeting_id: str = MEETING_A,
    title: str = "Mobile Application Planning",
    source_type: str = "decision",
    content: str = "Decision made in the meeting: complete the mobile application launch by Friday.",
    score: float = 0.92,
    date: str = "2026-09-15T10:00:00+00:00",
) -> RetrievedPassage:
    return RetrievedPassage(
        meeting_id=meeting_id,
        meeting_title=title,
        meeting_date=date,
        source_type=source_type,
        source_id="dec-1",
        chunk_index=0,
        content=content,
        score=score,
        status="COMPLETED",
    )


class FakeSearch:
    """Retrieval stand-in. Records the query it was given."""

    def __init__(self, passages: Optional[List[RetrievedPassage]] = None) -> None:
        self._passages = passages if passages is not None else [passage()]
        self.queries: List[str] = []
        self.is_configured = True

    async def retrieve(self, query, *, top_k=None, filters=None):
        self.queries.append(query)
        return list(self._passages)


def answer_json(**overrides: Any) -> str:
    payload = {
        "answer": "The team decided to complete the mobile application launch by Friday.",
        "answer_found": True,
        "used_meeting_ids": [MEETING_A],
        "confidence": "high",
    }
    payload.update(overrides)
    return json.dumps(payload)


def service(search: FakeSearch, groq: StubGroqClient, *, attempts: int = 2) -> RAGService:
    return RAGService(search=search, llm=LLMService(groq, schema_retry_attempts=attempts))


# ========================================================== TEST 10: the flow
@pytest.mark.asyncio
class TestRAGAnswering:
    async def test_a_question_is_answered_from_retrieved_meeting_context(self):
        search = FakeSearch()
        groq = StubGroqClient([answer_json()])

        response = await service(search, groq).ask(
            "What deadline was decided for the mobile application?"
        )

        assert response.answer_found is True
        assert "Friday" in response.answer
        assert response.provider == "groq"
        assert search.queries == ["What deadline was decided for the mobile application?"]
        assert groq.calls == 1

    async def test_the_retrieved_context_is_what_reaches_groq(self):
        """Grounding starts with actually sending the evidence."""
        groq = StubGroqClient([answer_json()])
        await service(FakeSearch(), groq).ask("What deadline was decided?")

        user_prompt = groq.prompts[0]
        assert "complete the mobile application launch by Friday" in user_prompt
        assert f"MEETING_ID: {MEETING_A}" in user_prompt
        assert "MEETING: Mobile Application Planning" in user_prompt
        assert "SOURCE TYPE: decision" in user_prompt
        assert groq.system_prompts[0] == SYSTEM_PROMPT

    async def test_sources_are_returned_so_the_answer_can_be_checked(self):
        response = await service(FakeSearch(), StubGroqClient([answer_json()])).ask(
            "What deadline was decided?"
        )

        assert len(response.sources) == 1
        source = response.sources[0]
        assert source.meeting_id == MEETING_A
        assert source.meeting_title == "Mobile Application Planning"
        assert source.meeting_date.startswith("2026-09-15")
        assert source.source_type == "decision"
        assert "Friday" in source.excerpt

    async def test_one_question_costs_exactly_one_groq_request(self):
        groq = StubGroqClient([answer_json(), answer_json()])
        await service(FakeSearch(), groq).ask("What deadline was decided?")
        assert groq.calls == 1

    async def test_the_model_that_answered_is_reported(self):
        response = await service(FakeSearch(), StubGroqClient([answer_json()], model="m-1")).ask(
            "What deadline?"
        )
        assert (response.provider, response.model) == ("groq", "m-1")

    async def test_timing_and_meeting_count_are_reported(self):
        response = await service(FakeSearch(), StubGroqClient([answer_json()])).ask(
            "What deadline was decided?"
        )
        assert response.searched_meetings == 1
        assert response.took_ms >= 0


# ============================================= TEST 11: no hallucination
@pytest.mark.asyncio
class TestNoHallucination:
    async def test_no_retrieval_means_no_groq_call_and_an_honest_answer(self):
        groq = StubGroqClient([answer_json()])

        response = await service(FakeSearch(passages=[]), groq).ask(
            "What did we decide about the Mars colony?"
        )

        assert response.answer_found is False
        assert response.answer == NO_CONTEXT_ANSWER
        assert response.sources == []
        assert groq.calls == 0, "never pay for a request to say 'I don't know'"

    async def test_a_not_found_answer_returns_no_sources(self):
        not_found = answer_json(
            answer="The meeting records do not contain that information.",
            answer_found=False, used_meeting_ids=[], confidence="low",
        )
        response = await service(FakeSearch(), StubGroqClient([not_found])).ask(
            "What is the CEO's home address?"
        )

        assert response.answer_found is False
        assert response.sources == []
        assert "do not contain" in response.answer

    async def test_a_cited_meeting_that_was_never_retrieved_is_discarded(self):
        """Citing a meeting the model was not shown is a hallucinated source."""
        invented = answer_json(used_meeting_ids=[MEETING_A, "a-meeting-that-does-not-exist"])
        response = await service(FakeSearch(), StubGroqClient([invented])).ask(
            "What deadline was decided?"
        )

        returned = {source.meeting_id for source in response.sources}
        assert "a-meeting-that-does-not-exist" not in returned
        assert returned == {MEETING_A}

    async def test_the_prompt_forbids_outside_knowledge(self):
        lowered = SYSTEM_PROMPT.lower()
        assert "only the meeting context" in lowered or "only the meeting records" in lowered
        assert "never invent" in lowered
        assert "answer_found" in SYSTEM_PROMPT
        assert "concise" in lowered
        assert "uncertainty" in lowered

    async def test_the_user_prompt_repeats_the_do_not_guess_rule(self):
        built = build_answer_prompt("Any question?", ["MEETING: A\nCONTENT: something"])
        assert "Do not guess." in built
        assert "answer_found" in built

    async def test_there_are_no_hardcoded_answers_in_the_rag_service(self):
        import inspect

        from app.services import rag_service

        source = inspect.getsource(rag_service)
        assert "Friday" not in source
        assert "database migration" not in source.lower()


# ========================================= TEST 12: several meetings at once
@pytest.mark.asyncio
class TestMultipleMeetings:
    async def test_context_from_several_meetings_is_kept_separate(self):
        search = FakeSearch(passages=[
            passage(meeting_id=MEETING_A, title="Mobile Application Planning",
                    content="We decided the launch is on Friday.", score=0.95),
            passage(meeting_id=MEETING_B, title="Launch Review",
                    content="We moved the launch to the next sprint.", score=0.88,
                    date="2026-09-18T10:00:00+00:00"),
        ])
        groq = StubGroqClient([answer_json(used_meeting_ids=[MEETING_A, MEETING_B])])

        response = await service(search, groq).ask(
            "Which meetings discussed the mobile application launch?"
        )

        user_prompt = groq.prompts[0]
        assert f"MEETING_ID: {MEETING_A}" in user_prompt
        assert f"MEETING_ID: {MEETING_B}" in user_prompt
        assert "Mobile Application Planning" in user_prompt and "Launch Review" in user_prompt
        assert response.searched_meetings == 2
        assert {s.meeting_id for s in response.sources} == {MEETING_A, MEETING_B}

    async def test_sources_are_one_per_meeting_not_one_per_passage(self):
        search = FakeSearch(passages=[
            passage(meeting_id=MEETING_A, content="First passage.", score=0.70),
            passage(meeting_id=MEETING_A, content="Stronger passage.", score=0.95),
            passage(meeting_id=MEETING_B, title="Launch Review", content="Other meeting.",
                    score=0.80),
        ])
        response = await service(
            search, StubGroqClient([answer_json(used_meeting_ids=[MEETING_A])])
        ).ask("What was decided?")

        assert len(response.sources) == 2
        best_for_a = next(s for s in response.sources if s.meeting_id == MEETING_A)
        assert "Stronger passage." in best_for_a.excerpt

    async def test_context_is_capped_rather_than_sending_the_whole_database(self):
        many = [
            passage(meeting_id=f"meeting-{index}", title=f"Meeting {index}",
                    content="A long passage about the project. " * 60, score=0.9 - index / 100)
            for index in range(40)
        ]
        groq = StubGroqClient([answer_json()])
        response = await service(FakeSearch(passages=many), groq).ask("What is going on?")

        from app.config import get_settings

        settings = get_settings()
        assert len(groq.prompts[0]) < settings.rag_max_context_chars * 2
        assert response.searched_meetings <= settings.rag_max_meetings_in_context


# ============================================== controlled failure handling
@pytest.mark.asyncio
class TestRAGFailureHandling:
    async def test_groq_failing_returns_the_meetings_not_a_made_up_answer(self):
        groq = StubGroqClient(error=rate_limit_error())

        response = await service(FakeSearch(), groq).ask("What deadline was decided?")

        assert response.answer_found is False
        assert "could not produce an answer" in response.answer
        assert response.sources, "retrieval succeeded, so the meetings are still useful"
        assert response.message
        assert groq.calls == 1, "no fallback provider, and no pointless second request"

    async def test_a_malformed_answer_gets_one_correction(self):
        groq = StubGroqClient(["I'm afraid I can't do that.", answer_json()])

        response = await service(FakeSearch(), groq).ask("What deadline?")

        assert response.answer_found is True
        assert groq.calls == 2
        assert "valid JSON object" in groq.prompts[1]

    async def test_an_answer_missing_required_fields_is_corrected_then_accepted(self):
        groq = StubGroqClient(['{"confidence": "high"}', answer_json()])
        response = await service(FakeSearch(), groq).ask("What deadline?")
        assert response.answer_found is True and groq.calls == 2

    async def test_persistently_malformed_output_is_never_shown(self):
        groq = StubGroqClient(["not json", "still not json"])
        response = await service(FakeSearch(), groq).ask("What deadline?")
        assert response.answer_found is False
        assert "not json" not in response.answer
        assert groq.calls == 2

    async def test_a_missing_groq_key_is_a_visible_configuration_error(self):
        groq = StubGroqClient(configured=False)
        with pytest.raises(LLMNotConfiguredError):
            await service(FakeSearch(), groq).ask("What deadline?")

    async def test_a_rejected_groq_key_is_surfaced_not_hidden(self):
        groq = StubGroqClient(error=auth_error())
        with pytest.raises(LLMNotConfiguredError) as exc:
            await service(FakeSearch(), groq).ask("What deadline?")
        assert exc.value.status_code == 503
        assert groq.calls == 1

    async def test_an_empty_question_never_reaches_retrieval(self):
        class RejectingSearch(FakeSearch):
            async def retrieve(self, query, *, top_k=None, filters=None):
                from app.utils.errors import InvalidQueryError

                if not (query or "").strip():
                    raise InvalidQueryError()
                return await super().retrieve(query, top_k=top_k, filters=filters)

        groq = StubGroqClient([answer_json()])
        with pytest.raises(Exception):
            await service(RejectingSearch(), groq).ask("   ")
        assert groq.calls == 0


# ============================================ answer schema (predictable shape)
class TestRAGAnswerValidation:
    def test_a_well_formed_answer_validates(self):
        answer = RAGAnswer.model_validate(json.loads(answer_json()))
        assert answer.answer_found is True
        assert answer.confidence == "high"
        assert answer.used_meeting_ids == [MEETING_A]

    def test_missing_optional_fields_get_safe_defaults(self):
        answer = RAGAnswer.model_validate({"answer": "Something was decided."})
        assert answer.answer_found is True
        assert answer.used_meeting_ids == []
        assert answer.confidence == "medium"

    def test_an_empty_answer_is_rejected(self):
        with pytest.raises(Exception):
            RAGAnswer.model_validate({"answer": "   "})

    def test_a_missing_answer_is_rejected(self):
        with pytest.raises(Exception):
            RAGAnswer.model_validate({"answer_found": True})

    @pytest.mark.parametrize("value,expected", [("false", False), ("no", False),
                                                ("true", True), (False, False), (None, True)])
    def test_answer_found_is_coerced_from_common_shapes(self, value, expected):
        answer = RAGAnswer.model_validate({"answer": "Something.", "answer_found": value})
        assert answer.answer_found is expected

    def test_object_shaped_meeting_ids_are_flattened(self):
        answer = RAGAnswer.model_validate(
            {"answer": "Something.", "used_meeting_ids": [{"meeting_id": MEETING_A}]}
        )
        assert answer.used_meeting_ids == [MEETING_A]

    def test_duplicate_meeting_ids_are_collapsed(self):
        answer = RAGAnswer.model_validate(
            {"answer": "Something.", "used_meeting_ids": [MEETING_A, MEETING_A]}
        )
        assert answer.used_meeting_ids == [MEETING_A]

    @pytest.mark.parametrize("value,expected", [("certain", "high"), ("moderate", "medium"),
                                                ("weird", "medium"), (None, "medium")])
    def test_confidence_aliases_are_mapped(self, value, expected):
        answer = RAGAnswer.model_validate({"answer": "Something.", "confidence": value})
        assert answer.confidence == expected

    def test_unexpected_extra_keys_are_ignored(self):
        answer = RAGAnswer.model_validate(
            {"answer": "Something.", "sentiment": "positive", "tokens": 42}
        )
        assert answer.answer == "Something."
        assert not hasattr(answer, "sentiment")
