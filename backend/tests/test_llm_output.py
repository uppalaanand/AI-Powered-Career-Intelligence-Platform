"""LLM structured-output tests (Milestone 2: schema enforcement, retries, action items).

The Grok client is replaced with a scripted stub, so these tests exercise the
real parsing, validation, retry and merge logic without calling xAI or needing
an API key.
"""

from __future__ import annotations

from typing import List

import pytest

from app.ai.llm_service import LLMService
from app.models.enums import ActionItemStatus, Priority
from app.schemas.intelligence import ActionItem, LLMMeetingIntelligence
from app.utils.errors import LLMInvalidResponseError
from app.utils.json_utils import extract_json_object

VALID_JSON = """
{
  "summary": "The team discussed the mobile application launch.",
  "key_points": ["Launch is on track", "API integration is the main dependency"],
  "decisions": ["Continue with the planned launch date"],
  "participants": ["Ravi", "Priya"],
  "action_items": [
    {"task": "Complete API integration", "assigned_to": "Ravi", "deadline": "Friday",
     "priority": "high", "status": "pending", "context": "Ravi agreed to finish it."},
    {"task": "Prepare UI testing report", "assigned_to": "Priya", "deadline": null,
     "priority": "medium", "status": "pending", "context": null}
  ]
}
"""


class ScriptedClient:
    """Returns queued responses in order and records the prompts it received."""

    def __init__(self, responses: List[str], model: str = "grok-test") -> None:
        self._responses = list(responses)
        self.model = model
        self.is_configured = True
        self.prompts: List[str] = []

    async def complete_json(self, *, system_prompt: str, user_prompt: str, **_) -> str:
        self.prompts.append(user_prompt)
        if not self._responses:
            raise AssertionError("ScriptedClient ran out of responses")
        return self._responses.pop(0)


# ------------------------------------------------------------ JSON recovery
class TestJsonExtraction:
    def test_plain_json_parses(self):
        assert extract_json_object('{"summary": "ok"}') == {"summary": "ok"}

    def test_markdown_fenced_json_parses(self):
        assert extract_json_object('```json\n{"summary": "ok"}\n```') == {"summary": "ok"}

    def test_json_with_surrounding_prose_parses(self):
        raw = 'Here is the analysis you asked for:\n{"summary": "ok"}\nHope that helps!'
        assert extract_json_object(raw) == {"summary": "ok"}

    def test_trailing_comma_is_repaired(self):
        assert extract_json_object('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}

    def test_smart_quotes_are_repaired(self):
        assert extract_json_object('{\u201csummary\u201d: \u201cok\u201d}') == {"summary": "ok"}

    def test_braces_inside_strings_do_not_break_extraction(self):
        assert extract_json_object('{"summary": "uses {braces} inside"}')["summary"] == "uses {braces} inside"

    def test_unparseable_text_returns_none(self):
        assert extract_json_object("I'm sorry, I cannot help with that.") is None

    def test_empty_input_returns_none(self):
        assert extract_json_object("") is None


# ------------------------------------------------------------ schema checks
class TestSchemaValidation:
    def test_valid_payload_validates(self):
        result = LLMMeetingIntelligence.model_validate(extract_json_object(VALID_JSON))
        assert len(result.action_items) == 2
        assert result.action_items[0].priority is Priority.HIGH

    def test_missing_optional_lists_default_to_empty(self):
        result = LLMMeetingIntelligence.model_validate({"summary": "Only a summary."})
        assert result.key_points == [] and result.decisions == []
        assert result.action_items == [] and result.participants == []

    def test_missing_summary_is_rejected(self):
        with pytest.raises(Exception):
            LLMMeetingIntelligence.model_validate({"key_points": ["a"]})

    def test_unexpected_extra_keys_are_ignored(self):
        result = LLMMeetingIntelligence.model_validate(
            {"summary": "Fine.", "sentiment": "positive", "confidence": 0.9}
        )
        assert result.summary == "Fine."
        assert not hasattr(result, "sentiment")

    def test_object_shaped_key_points_are_flattened(self):
        result = LLMMeetingIntelligence.model_validate(
            {"summary": "s", "key_points": [{"text": "Launch is on track"}]}
        )
        assert result.key_points == ["Launch is on track"]

    def test_action_items_without_a_task_are_dropped(self):
        result = LLMMeetingIntelligence.model_validate(
            {"summary": "s", "action_items": [{"task": ""}, {"task": "Real task"}]}
        )
        assert [item.task for item in result.action_items] == ["Real task"]


# ------------------------------------------------------- action item fields
class TestActionItemValidation:
    def test_missing_participant_becomes_null(self):
        item = ActionItem(task="Ship it", assigned_to=None)
        assert item.assigned_to is None

    @pytest.mark.parametrize("placeholder", ["unknown", "N/A", "not specified", "TBD", ""])
    def test_placeholder_assignee_becomes_null(self, placeholder):
        assert ActionItem(task="Ship it", assigned_to=placeholder).assigned_to is None

    @pytest.mark.parametrize("placeholder", ["null", "none", "not mentioned", "-"])
    def test_placeholder_deadline_becomes_null(self, placeholder):
        assert ActionItem(task="Ship it", deadline=placeholder).deadline is None

    def test_real_deadline_is_kept_as_spoken(self):
        assert ActionItem(task="Ship it", deadline="Friday").deadline == "Friday"

    def test_invalid_priority_is_rejected(self):
        with pytest.raises(Exception):
            ActionItem(task="Ship it", priority="extremely urgent indeed")

    @pytest.mark.parametrize("alias,expected", [
        ("urgent", Priority.HIGH), ("critical", Priority.HIGH), ("P1", Priority.HIGH),
        ("normal", Priority.MEDIUM), ("moderate", Priority.MEDIUM),
        ("minor", Priority.LOW), ("nice to have", Priority.LOW),
    ])
    def test_common_priority_aliases_are_mapped(self, alias, expected):
        assert ActionItem(task="Ship it", priority=alias).priority is expected

    def test_priority_defaults_to_medium(self):
        assert ActionItem(task="Ship it").priority is Priority.MEDIUM

    def test_invalid_status_is_rejected(self):
        with pytest.raises(Exception):
            ActionItem(task="Ship it", status="halfway there maybe")

    @pytest.mark.parametrize("alias,expected", [
        ("open", ActionItemStatus.PENDING), ("todo", ActionItemStatus.PENDING),
        ("in progress", ActionItemStatus.IN_PROGRESS), ("wip", ActionItemStatus.IN_PROGRESS),
        ("done", ActionItemStatus.COMPLETED), ("on hold", ActionItemStatus.BLOCKED),
    ])
    def test_common_status_aliases_are_mapped(self, alias, expected):
        assert ActionItem(task="Ship it", status=alias).status is expected

    def test_status_defaults_to_pending(self):
        assert ActionItem(task="Ship it").status is ActionItemStatus.PENDING

    def test_blank_task_is_rejected(self):
        with pytest.raises(Exception):
            ActionItem(task="   ")


# --------------------------------------------------- service-level behaviour
@pytest.mark.asyncio
class TestLLMService:
    async def test_valid_response_produces_intelligence(self):
        service = LLMService(client=ScriptedClient([VALID_JSON]))
        result, metadata = await service.analyze_transcript("A short transcript.")
        assert result.summary.startswith("The team discussed")
        assert len(result.action_items) == 2
        assert metadata["chunk_count"] == 1
        assert metadata["was_chunked"] is False

    async def test_malformed_json_is_retried_then_succeeds(self):
        client = ScriptedClient(["I'm afraid I can't do that.", VALID_JSON])
        service = LLMService(client=client)
        result, _ = await service.analyze_transcript("A short transcript.")
        assert len(result.action_items) == 2
        assert len(client.prompts) == 2
        assert "valid JSON object" in client.prompts[1]

    async def test_schema_violation_is_retried_with_a_correction(self):
        client = ScriptedClient(['{"key_points": ["no summary field"]}', VALID_JSON])
        service = LLMService(client=client)
        result, _ = await service.analyze_transcript("A short transcript.")
        assert result.summary
        assert len(client.prompts) == 2

    async def test_persistent_bad_output_raises_a_controlled_error(self):
        client = ScriptedClient(["not json", "still not json"])
        service = LLMService(client=client)
        with pytest.raises(LLMInvalidResponseError) as exc:
            await service.analyze_transcript("A short transcript.")
        assert exc.value.code == "LLM_INVALID_RESPONSE"
        assert exc.value.status_code == 502

    async def test_empty_transcript_is_rejected_before_calling_the_model(self):
        client = ScriptedClient([])
        service = LLMService(client=client)
        with pytest.raises(LLMInvalidResponseError):
            await service.analyze_transcript("   ")
        assert client.prompts == []

    async def test_long_transcript_is_chunked_and_merged(self):
        long_text = "The team discussed the launch in detail. " * 800
        merged = """
        {"summary": "Merged summary of the whole meeting.",
         "key_points": ["Launch on track"], "decisions": [],
         "participants": ["Ravi"], "action_items": []}
        """
        # Enough chunk responses for every chunk, plus one merge response.
        client = ScriptedClient([VALID_JSON] * 12 + [merged])
        service = LLMService(client=client)
        result, metadata = await service.analyze_transcript(long_text)
        assert metadata["was_chunked"] is True
        assert metadata["chunk_count"] > 1
        assert result.summary
        # De-duplication across chunks: the same two tasks must not multiply.
        assert len(result.action_items) == 2

    async def test_local_merge_is_used_when_the_merge_call_fails(self):
        long_text = "The team discussed the launch in detail. " * 800
        client = ScriptedClient([VALID_JSON] * 12 + ["garbage", "garbage"])
        service = LLMService(client=client)
        result, _ = await service.analyze_transcript(long_text)
        assert result.summary
        assert len(result.action_items) == 2

    async def test_a_single_failed_chunk_does_not_lose_the_others(self):
        long_text = "The team discussed the launch in detail. " * 800
        responses = ["garbage", "garbage"] + [VALID_JSON] * 20
        service = LLMService(client=ScriptedClient(responses))
        result, metadata = await service.analyze_transcript(long_text)
        assert result.summary
        assert metadata["successful_chunks"] >= 1
