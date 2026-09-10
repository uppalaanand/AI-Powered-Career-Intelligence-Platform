"""LLM analysis engine - transcript in, validated meeting intelligence out.

One instance drives exactly *one* provider. Choosing between providers and
falling back is the orchestrator's job (``app.ai.llm_orchestrator``); this file
only knows how to turn a transcript into validated intelligence using whichever
provider it was handed:

    prepare prompt -> call provider -> parse JSON -> validate against Pydantic
    -> retry on malformed output -> chunk long transcripts -> aggregate

Two aggregation modes:

* <= one chunk : single pass.
* > one chunk  : map/reduce. Each chunk is analysed, then the partial results
  are merged. The merge is attempted by the model first (it writes a better
  connected summary); if that call fails or returns junk, a deterministic local
  merge takes over so a long meeting still produces a usable result.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from app.ai.chunking import ChunkPlan, plan_chunks
from app.ai.providers import LLMProvider, ProviderCategory, category_of
from app.ai.providers.grok_provider import GrokProvider
from app.ai.prompts.meeting_intelligence_prompt import (
    SYSTEM_PROMPT,
    build_chunk_prompt,
    build_merge_prompt,
    build_single_pass_prompt,
)
from app.config import get_logger
from app.models.enums import Priority
from app.schemas.intelligence import ActionItem, LLMMeetingIntelligence
from app.utils.errors import LLMInvalidResponseError
from app.utils.json_utils import dumps, extract_json_object
from app.utils.text import collapse_whitespace, normalize_person_name, truncate

logger = get_logger(__name__)

# One extra attempt with a corrective instruction when JSON validation fails.
# Override per instance (the orchestrator reads LLM_SCHEMA_RETRY_ATTEMPTS) to
# spend the strict minimum of a free-tier quota.
SCHEMA_RETRY_ATTEMPTS = 2
# Chunks analysed at the same time. Small on purpose: friendlier to rate limits.
CHUNK_CONCURRENCY = 3

# A provider that fails this way is not going to answer the next chunk either.
# Seeing one of these stops the engine from spending 39 more requests proving it.
FATAL_CATEGORIES = {
    ProviderCategory.NOT_CONFIGURED,
    ProviderCategory.AUTH,
    ProviderCategory.RATE_LIMIT,
    ProviderCategory.MODEL_UNAVAILABLE,
    ProviderCategory.NETWORK,
    ProviderCategory.TIMEOUT,
    ProviderCategory.SERVER_ERROR,
}


class LLMService:
    """Analysis engine bound to a single provider."""

    def __init__(
        self,
        client: Optional[LLMProvider] = None,
        *,
        schema_retry_attempts: Optional[int] = None,
    ) -> None:
        self._client = client or GrokProvider()
        self._schema_attempts = max(1, schema_retry_attempts or SCHEMA_RETRY_ATTEMPTS)

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def provider_name(self) -> str:
        return getattr(self._client, "name", "grok")

    @property
    def provider_label(self) -> str:
        return getattr(self._client, "label", "Grok (xAI)")

    @property
    def is_configured(self) -> bool:
        return self._client.is_configured

    # --------------------------------------------------------------- public
    async def analyze_transcript(
        self, transcript: str, *, meeting_title: str = ""
    ) -> Tuple[LLMMeetingIntelligence, Dict[str, Any]]:
        """Return validated intelligence plus metadata about how it was produced."""
        text = (transcript or "").strip()
        if not text:
            raise LLMInvalidResponseError(
                "There is no transcript text to analyse.", code="EMPTY_TRANSCRIPT", status_code=422
            )

        plan: ChunkPlan = plan_chunks(text)
        metadata: Dict[str, Any] = {
            "provider": self.provider_name,
            "provider_label": self.provider_label,
            "model": self.model,
            "chunk_count": plan.count,
            "was_chunked": plan.was_chunked,
            "was_truncated": plan.was_truncated,
            "transcript_chars": plan.total_chars,
        }

        if plan.count <= 1:
            prompt = build_single_pass_prompt(plan.chunks[0].text, meeting_title)
            result = await self._request_validated(prompt, max_tokens=4000)
            return result, metadata

        logger.info(
            "Analysing %s chunks for '%s' with %s",
            plan.count, meeting_title or "meeting", self.provider_label,
        )
        partials, fatal = await self._analyze_chunks(plan, meeting_title)

        if not partials:
            # Nothing usable. Surface the provider's own failure when there was
            # one, so the orchestrator can classify it and fall back correctly.
            raise fatal or LLMInvalidResponseError(
                "None of the transcript sections could be analysed. Try again in a moment."
            )

        metadata["successful_chunks"] = len(partials)
        if fatal is not None:
            # The provider died part-way. Keep the sections that did come back
            # rather than re-running the whole meeting on another provider's
            # quota, and tell the caller the result is incomplete.
            metadata["partial"] = True
            metadata["degraded_reason"] = category_of(fatal)
            logger.warning(
                "%s stopped after %s/%s sections (%s); merging what completed.",
                self.provider_label, len(partials), plan.count, category_of(fatal),
            )
            return self._local_merge(partials), metadata

        merged = await self._merge(partials, meeting_title)
        return merged, metadata

    async def check_connection(self) -> Dict[str, Any]:
        return await self._client.check_connection()

    # ------------------------------------------------------------- map step
    async def _analyze_chunks(
        self, plan: ChunkPlan, meeting_title: str
    ) -> Tuple[List[LLMMeetingIntelligence], Optional[Exception]]:
        """Analyse every chunk, tolerating individual failures.

        Returns the sections that succeeded plus the first *fatal* provider
        error, if one occurred. A fatal error (bad key, quota gone, model
        missing, provider unreachable) stops any chunk that has not started:
        the answer will not improve, and each further request is a free-tier
        call spent on a provider that is already broken.
        """
        semaphore = asyncio.Semaphore(CHUNK_CONCURRENCY)
        abort = asyncio.Event()
        fatal: Optional[Exception] = None

        async def analyze_one(index: int, chunk_text: str) -> Optional[LLMMeetingIntelligence]:
            nonlocal fatal
            if abort.is_set():
                return None
            async with semaphore:
                if abort.is_set():
                    return None
                prompt = build_chunk_prompt(chunk_text, index, plan.count, meeting_title)
                try:
                    return await self._request_validated(prompt, max_tokens=3000)
                except Exception as exc:  # noqa: BLE001
                    if category_of(exc) in FATAL_CATEGORIES:
                        if fatal is None:
                            fatal = exc
                        abort.set()
                        logger.error(
                            "Chunk %s/%s hit a fatal %s failure (%s); stopping this provider.",
                            index + 1, plan.count, self.provider_label, category_of(exc),
                        )
                        return None
                    # One bad chunk should not lose the other 39.
                    logger.error("Chunk %s/%s failed: %s", index + 1, plan.count, exc)
                    return None

        results = await asyncio.gather(
            *(analyze_one(chunk.index, chunk.text) for chunk in plan.chunks)
        )
        return [result for result in results if result is not None], fatal

    # ---------------------------------------------------------- reduce step
    async def _merge(
        self, partials: List[LLMMeetingIntelligence], meeting_title: str
    ) -> LLMMeetingIntelligence:
        payloads = [dumps(part.model_dump(mode="json")) for part in partials]
        try:
            prompt = build_merge_prompt(payloads, meeting_title)
            merged = await self._request_validated(prompt, max_tokens=4000)
            if merged.summary:
                # Union the lists: the merge model may drop an item, and losing a
                # real action item is worse than an occasional near-duplicate.
                return self._union(merged, partials)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Model merge failed, falling back to local merge: %s", exc)

        return self._local_merge(partials)

    def _union(
        self, merged: LLMMeetingIntelligence, partials: List[LLMMeetingIntelligence]
    ) -> LLMMeetingIntelligence:
        combined = self._local_merge(partials)
        return LLMMeetingIntelligence(
            summary=merged.summary or combined.summary,
            key_points=_dedupe_strings(merged.key_points + combined.key_points),
            decisions=_dedupe_strings(merged.decisions + combined.decisions),
            participants=_dedupe_names(merged.participants + combined.participants),
            action_items=_dedupe_action_items(merged.action_items + combined.action_items),
        )

    @staticmethod
    def _local_merge(partials: List[LLMMeetingIntelligence]) -> LLMMeetingIntelligence:
        """Deterministic fallback merge - no model call, no invented content."""
        summaries = [part.summary for part in partials if part.summary]
        key_points: List[str] = []
        decisions: List[str] = []
        participants: List[str] = []
        action_items: List[ActionItem] = []

        for part in partials:
            key_points.extend(part.key_points)
            decisions.extend(part.decisions)
            participants.extend(part.participants)
            action_items.extend(part.action_items)

        return LLMMeetingIntelligence(
            summary=collapse_whitespace(" ".join(summaries)),
            key_points=_dedupe_strings(key_points),
            decisions=_dedupe_strings(decisions),
            participants=_dedupe_names(participants),
            action_items=_dedupe_action_items(action_items),
        )

    # ------------------------------------------------- request + validation
    async def _request_validated(self, prompt: str, *, max_tokens: int) -> LLMMeetingIntelligence:
        """Call the model and validate the reply, correcting once on failure."""
        last_error: Optional[str] = None
        current_prompt = prompt

        for attempt in range(1, self._schema_attempts + 1):
            raw = await self._client.complete_json(
                system_prompt=SYSTEM_PROMPT, user_prompt=current_prompt, max_tokens=max_tokens
            )
            parsed = extract_json_object(raw)

            if parsed is None:
                last_error = "the reply was not valid JSON"
                logger.warning(
                    "Attempt %s: unparseable JSON. First 200 chars: %s",
                    attempt, truncate(raw, 200),
                )
            elif not isinstance(parsed, dict):
                last_error = "the reply was not a JSON object"
            else:
                try:
                    return LLMMeetingIntelligence.model_validate(parsed)
                except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
                    last_error = str(exc)[:400]
                    logger.warning("Attempt %s: schema validation failed: %s", attempt, last_error)

            if attempt < self._schema_attempts:
                current_prompt = (
                    f"{prompt}\n\nYour previous reply could not be used because "
                    f"{last_error}. Return ONLY a valid JSON object matching the schema "
                    "exactly, with no markdown fences and no commentary."
                )

        raise LLMInvalidResponseError(
            f"{self.provider_label} returned a response that did not match the required "
            f"format after {self._schema_attempts} attempt(s).",
            details={
                "provider": self.provider_name,
                "category": ProviderCategory.INVALID_RESPONSE,
                "reason": last_error,
            },
        )


# --------------------------------------------------------------- de-duping
def _dedupe_strings(values: List[str]) -> List[str]:
    """Keep order, drop repeats and near-repeats (same words, different casing)."""
    seen: set = set()
    output: List[str] = []
    for value in values:
        cleaned = collapse_whitespace(value)
        if not cleaned:
            continue
        key = "".join(character for character in cleaned.lower() if character.isalnum())
        if key and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def _dedupe_names(values: List[str]) -> List[str]:
    seen: set = set()
    output: List[str] = []
    for value in values:
        key = normalize_person_name(value)
        if key and key not in seen:
            seen.add(key)
            output.append(collapse_whitespace(value))
    return output


def _dedupe_action_items(items: List[ActionItem]) -> List[ActionItem]:
    """Same task + same owner = one item. The richer of the two survives."""
    merged: Dict[str, ActionItem] = {}
    order: List[str] = []

    for item in items:
        task_key = "".join(character for character in item.task.lower() if character.isalnum())
        owner_key = normalize_person_name(item.assigned_to or "")
        key = f"{task_key}|{owner_key}"
        if not task_key:
            continue

        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            order.append(key)
            continue

        merged[key] = ActionItem(
            task=existing.task,
            assigned_to=existing.assigned_to or item.assigned_to,
            deadline=existing.deadline or item.deadline,
            priority=_higher_priority(existing.priority, item.priority),
            status=existing.status,
            context=existing.context or item.context,
        )
    return [merged[key] for key in order]


def _higher_priority(left: Priority, right: Priority) -> Priority:
    rank = {Priority.HIGH: 3, Priority.MEDIUM: 2, Priority.LOW: 1}
    return left if rank[left] >= rank[right] else right
