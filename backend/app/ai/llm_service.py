"""LLM service - every language-model task in the application.

The single entry point for LLM work. Everything goes through here, and from here
through :class:`~app.ai.groq_client.GroqClient` - the only file that talks to
Groq, the application's only LLM provider:

    Milestone 2  analyze_transcript()   transcript -> validated meeting intelligence
    Milestone 3  generate_validated()   RAG prompt -> validated grounded answer

Both share one request-and-validate loop, so they get the same guarantees:
parse the JSON, validate it against a Pydantic model, correct the model once if
the reply is malformed, and never let unvalidated output reach the caller.

Meeting analysis has two aggregation modes:

* <= one chunk : single pass - ONE request produces the summary, key points,
  decisions, participants and action items (with owners, deadlines,
  priorities and statuses) together. Nothing is requested field by field.
* > one chunk  : map/reduce. Each chunk is analysed, then the partial results
  are merged. The merge is attempted by the model first (it writes a better
  connected summary) when the merge prompt fits the token budget; otherwise, or
  if that call fails, a deterministic local merge takes over at no cost.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from app.ai.chunking import ChunkPlan, estimate_tokens, plan_chunks
from app.ai.groq_client import ErrorCategory, GroqClient, category_of
from app.ai.prompts.meeting_intelligence_prompt import (
    SYSTEM_PROMPT,
    build_chunk_prompt,
    build_merge_prompt,
    build_single_pass_prompt,
)
from app.config import get_logger, get_settings
from app.models.enums import Priority
from app.schemas.intelligence import ActionItem, LLMMeetingIntelligence
from app.utils.errors import LLMInvalidResponseError
from app.utils.json_utils import dumps, extract_json_object
from app.utils.text import collapse_whitespace, normalize_person_name, truncate

logger = get_logger(__name__)
T = TypeVar("T")

# A failure of this kind is not going to fix itself on the next chunk. Seeing
# one stops the analysis from spending 39 more requests proving it.
FATAL_CATEGORIES = {
    ErrorCategory.NOT_CONFIGURED,
    ErrorCategory.AUTH,
    ErrorCategory.RATE_LIMIT,
    ErrorCategory.REQUEST_TOO_LARGE,
    ErrorCategory.MODEL_UNAVAILABLE,
    ErrorCategory.NETWORK,
    ErrorCategory.TIMEOUT,
    ErrorCategory.SERVER_ERROR,
}


class LLMService:
    """All LLM work, over one Groq client."""

    def __init__(
        self,
        client: Optional[GroqClient] = None,
        *,
        schema_retry_attempts: Optional[int] = None,
    ) -> None:
        settings = get_settings()
        self._client = client or GroqClient()
        attempts = (
            schema_retry_attempts
            if schema_retry_attempts is not None
            else settings.llm_schema_retry_attempts
        )
        self._schema_attempts = max(1, attempts)
        self._chunk_concurrency = max(1, settings.llm_chunk_concurrency)
        self._max_output_tokens = settings.llm_max_output_tokens
        self._chunk_max_output_tokens = settings.llm_chunk_max_output_tokens
        self._max_request_tokens = settings.llm_max_request_tokens

    @property
    def model(self) -> str:
        return self._client.model

    @property
    def provider_name(self) -> str:
        return getattr(self._client, "name", "groq")

    @property
    def provider_label(self) -> str:
        return getattr(self._client, "label", "Groq")

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
            result = await self._request_validated(prompt, max_tokens=self._max_output_tokens)
            return result, metadata

        logger.info(
            "Analysing %s chunks for '%s' with %s",
            plan.count, meeting_title or "meeting", self.provider_label,
        )
        partials, fatal = await self._analyze_chunks(plan, meeting_title)

        if not partials:
            # Nothing usable. Surface Groq's own failure when there was one, so
            # the caller sees the real reason (bad key, quota) not a generic one.
            raise fatal or LLMInvalidResponseError(
                "None of the transcript sections could be analysed. Try again in a moment."
            )

        metadata["successful_chunks"] = len(partials)
        if fatal is not None:
            # Groq stopped part-way (quota, outage). Keep the sections that did
            # come back rather than re-running the whole meeting, and tell the
            # caller the result is incomplete.
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

        Returns the sections that succeeded plus the first *fatal* error, if
        one occurred. A fatal error (bad key, quota gone, model missing, Groq
        unreachable) stops any chunk that has not started: the answer will not
        improve, and each further request would be quota spent for nothing.

        Chunks run ``LLM_CHUNK_CONCURRENCY`` at a time (default 1) because a
        free Groq plan limits tokens per minute; parallel chunks would mostly
        buy 429s.
        """
        semaphore = asyncio.Semaphore(self._chunk_concurrency)
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
                    return await self._request_validated(
                        prompt, max_tokens=self._chunk_max_output_tokens
                    )
                except Exception as exc:  # noqa: BLE001
                    if category_of(exc) in FATAL_CATEGORIES:
                        if fatal is None:
                            fatal = exc
                        abort.set()
                        logger.error(
                            "Chunk %s/%s hit a fatal %s failure (%s); stopping the analysis.",
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
        prompt = build_merge_prompt(payloads, meeting_title)

        # A merge prompt that cannot fit the plan's per-request token budget
        # would only earn a 413. Merge locally instead - free and deterministic.
        requested = estimate_tokens(SYSTEM_PROMPT + prompt) + self._max_output_tokens
        if requested > self._max_request_tokens:
            logger.info(
                "Merge prompt (~%s tokens incl. output) exceeds LLM_MAX_REQUEST_TOKENS=%s; "
                "merging %s sections locally without a Groq request.",
                requested, self._max_request_tokens, len(partials),
            )
            return self._local_merge(partials)

        try:
            merged = await self._request_validated(prompt, max_tokens=self._max_output_tokens)
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
    async def generate_validated(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        validate: Callable[[Dict[str, Any]], T],
        max_tokens: int,
        purpose: str = "LLM request",
    ) -> Tuple[T, Dict[str, Any]]:
        """Send one prompt to Groq and return a *validated* result.

        The shared engine behind meeting analysis and RAG answers::

            request -> parse JSON -> validate -> result
                           |             |
                           +-- fails ----+--> one corrective re-prompt
                                              (LLM_SCHEMA_RETRY_ATTEMPTS)

        Output that still fails validation raises ``LLMInvalidResponseError``,
        so a malformed answer never reaches an API response or the database.
        """
        last_error: Optional[str] = None
        current_prompt = user_prompt

        for attempt in range(1, self._schema_attempts + 1):
            raw = await self._client.complete_json(
                system_prompt=system_prompt, user_prompt=current_prompt, max_tokens=max_tokens
            )
            parsed = extract_json_object(raw)

            if parsed is None:
                last_error = "the reply was not valid JSON"
                logger.warning(
                    "%s attempt %s: unparseable JSON. First 200 chars: %s",
                    purpose, attempt, truncate(raw, 200),
                )
            elif not isinstance(parsed, dict):
                last_error = "the reply was not a JSON object"
            else:
                try:
                    result = validate(parsed)
                    return result, {
                        "provider": self.provider_name,
                        "provider_label": self.provider_label,
                        "model": self.model,
                    }
                except Exception as exc:  # noqa: BLE001 - pydantic ValidationError
                    last_error = str(exc)[:400]
                    logger.warning(
                        "%s attempt %s: schema validation failed: %s", purpose, attempt, last_error
                    )

            if attempt < self._schema_attempts:
                current_prompt = (
                    f"{user_prompt}\n\nYour previous reply could not be used because "
                    f"{last_error}. Return ONLY a valid JSON object matching the schema "
                    "exactly, with no markdown fences and no commentary."
                )

        raise LLMInvalidResponseError(
            f"{self.provider_label} returned a response that did not match the required "
            f"format after {self._schema_attempts} attempt(s).",
            details={
                "provider": self.provider_name,
                "category": ErrorCategory.INVALID_RESPONSE,
                "reason": last_error,
            },
        )

    async def _request_validated(self, prompt: str, *, max_tokens: int) -> LLMMeetingIntelligence:
        """Meeting-analysis request: the shared loop with the intelligence schema."""
        result, _ = await self.generate_validated(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            validate=LLMMeetingIntelligence.model_validate,
            max_tokens=max_tokens,
            purpose="Meeting analysis",
        )
        return result


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
