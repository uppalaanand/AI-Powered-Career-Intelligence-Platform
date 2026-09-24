"""Retrieval-Augmented Generation over meeting records (Milestone 3, Task 5).

    question -> semantic search -> grounded context -> existing LLM chain -> answer

The LLM layer here is the *same* ``LLMService`` that Milestone 2 uses for
meeting analysis, which sends everything to Groq - the only LLM provider. RAG is
a second capability of that one service, not a second LLM integration.

Cost of one question: **one** embedding request, **one** vector search, **one**
database read, and **one** LLM request (two or three only if a provider is
down). Retrieval finding nothing short-circuits before the LLM is called at all,
because there is no point paying a model to tell us what we already know.

Grounding
---------
Three mechanisms, because a prompt alone is not enough:

1. Context blocks are labelled with meeting, id, date and source type, and are
   grouped per meeting so separate meetings never blur together.
2. The prompt forbids outside knowledge and requires ``answer_found: false``
   when the context does not contain the answer.
3. ``used_meeting_ids`` is checked against what was actually retrieved - an id
   the model invented is discarded rather than shown as a citation.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Sequence

from app.ai.llm_service import LLMService
from app.ai.prompts.rag_prompt import (
    NO_CONTEXT_ANSWER,
    SYSTEM_PROMPT,
    build_answer_prompt,
    format_context_block,
)
from app.config import get_logger, get_settings
from app.schemas.rag import AskResponse, RAGAnswer, SourceReference
from app.schemas.search import SearchFilters
from app.services.semantic_search_service import RetrievedPassage, SemanticSearchService
from app.utils.errors import AppError, LLMNotConfiguredError
from app.utils.text import truncate

logger = get_logger(__name__)

#: Answer returned when every provider fails after retrieval succeeded. The
#: sources are still shown, so the user gets the meetings even without prose.
LLM_UNAVAILABLE_MESSAGE = (
    "The meetings below look relevant, but the AI service could not produce an "
    "answer right now. Please try again in a moment."
)


class RAGService:
    def __init__(
        self,
        search: Optional[SemanticSearchService] = None,
        llm: Optional[LLMService] = None,
    ) -> None:
        self._search = search or SemanticSearchService()
        self._llm = llm or LLMService()

    @property
    def is_configured(self) -> bool:
        return self._search.is_configured and self._llm.is_configured

    # ----------------------------------------------------------------- ask
    async def ask(
        self,
        question: str,
        *,
        top_k: Optional[int] = None,
        filters: Optional[SearchFilters] = None,
    ) -> AskResponse:
        started = time.perf_counter()
        settings = get_settings()

        passages = await self._search.retrieve(
            question, top_k=top_k or settings.rag_top_k, filters=filters
        )

        if not passages:
            # Nothing retrieved: answer honestly without spending an LLM call.
            logger.info("RAG found no relevant passages; answering without the LLM.")
            return AskResponse(
                question=question,
                answer=NO_CONTEXT_ANSWER,
                answer_found=False,
                confidence="low",
                sources=[],
                searched_meetings=0,
                took_ms=int((time.perf_counter() - started) * 1000),
                message="No relevant meetings were found for this question.",
            )

        selected = _limit_context(passages)
        blocks = _build_context_blocks(selected)
        meetings_used = len({passage.meeting_id for passage in selected})
        logger.info(
            "RAG retrieved %s passage(s) across %s meeting(s); asking the LLM.",
            len(selected), meetings_used,
        )

        try:
            answer, metadata = await self._answer(question, blocks)
        except LLMNotConfiguredError:
            # A missing API key is an operator problem, not a transient one.
            # Surfacing it as a 503 tells them what to fix; hiding it inside a
            # 200 response would make the app look mysteriously broken.
            raise
        except AppError as exc:
            # Retrieval worked, generation did not. Return the meetings we found
            # with a controlled message rather than inventing an answer.
            logger.error("RAG generation failed: %s - %s", exc.code, exc.message)
            return AskResponse(
                question=question,
                answer=LLM_UNAVAILABLE_MESSAGE,
                answer_found=False,
                confidence="low",
                sources=_build_sources(selected, None),
                searched_meetings=meetings_used,
                took_ms=int((time.perf_counter() - started) * 1000),
                message=exc.message,
            )

        cited = _valid_citations(answer.used_meeting_ids, selected)
        sources = _build_sources(selected, cited if answer.answer_found else None)

        return AskResponse(
            question=question,
            answer=answer.answer,
            answer_found=answer.answer_found,
            confidence=answer.confidence,
            sources=sources if answer.answer_found else [],
            provider=metadata.get("provider"),
            model=metadata.get("model"),
            searched_meetings=meetings_used,
            took_ms=int((time.perf_counter() - started) * 1000),
        )

    # ----------------------------------------------------------- internals
    async def _answer(self, question: str, blocks: List[str]):
        """One LLM request through the existing fallback chain.

        ``RAGAnswer.model_validate`` runs inside LLMService's request loop, so
        a reply that parses but does not satisfy the schema earns one corrective
        re-prompt and is otherwise rejected - the same rule Milestone 2 applies
        to meeting analysis, and the reason a malformed answer can never reach
        the API.
        """
        return await self._llm.generate_validated(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_answer_prompt(question, blocks),
            max_tokens=1200,
            purpose="Meeting question answering",
            validate=RAGAnswer.model_validate,
        )


# ------------------------------------------------------------------ context
def _limit_context(passages: Sequence[RetrievedPassage]) -> List[RetrievedPassage]:
    """Trim retrieval down to what is worth sending.

    Two ceilings, both about keeping the prompt focused and the request cheap:
    a character budget, and a cap on how many distinct meetings appear. Passages
    arrive best-first, so trimming from the end drops the weakest evidence.
    """
    settings = get_settings()
    max_chars = settings.rag_max_context_chars
    max_meetings = settings.rag_max_meetings_in_context

    selected: List[RetrievedPassage] = []
    meetings: List[str] = []
    used_chars = 0

    for passage in passages:
        if passage.meeting_id not in meetings:
            if len(meetings) >= max_meetings:
                continue
            meetings.append(passage.meeting_id)
        cost = len(passage.content) + 120  # rough allowance for the block labels
        if used_chars + cost > max_chars and selected:
            break
        selected.append(passage)
        used_chars += cost

    return selected


def _build_context_blocks(passages: Sequence[RetrievedPassage]) -> List[str]:
    """Format context grouped by meeting, preserving meeting boundaries.

    Grouping matters for multi-meeting questions: if two meetings both mention a
    launch date, interleaving their passages invites the model to merge them
    into one wrong sentence.
    """
    by_meeting: Dict[str, List[RetrievedPassage]] = {}
    order: List[str] = []
    for passage in passages:
        if passage.meeting_id not in by_meeting:
            by_meeting[passage.meeting_id] = []
            order.append(passage.meeting_id)
        by_meeting[passage.meeting_id].append(passage)

    blocks: List[str] = []
    for meeting_id in order:
        for passage in by_meeting[meeting_id]:
            blocks.append(
                format_context_block(
                    meeting_id=passage.meeting_id,
                    meeting_title=passage.meeting_title,
                    meeting_date=passage.meeting_date or "unknown",
                    source_type=passage.source_type,
                    content=passage.content,
                )
            )
    return blocks


def _valid_citations(
    claimed: Sequence[str], passages: Sequence[RetrievedPassage]
) -> List[str]:
    """Keep only cited ids that were actually retrieved.

    A model citing a meeting it was never shown is hallucinating a source; that
    citation is dropped instead of being displayed as evidence.
    """
    retrieved = {passage.meeting_id for passage in passages}
    valid = [meeting_id for meeting_id in claimed if meeting_id in retrieved]
    invented = [meeting_id for meeting_id in claimed if meeting_id not in retrieved]
    if invented:
        logger.warning(
            "Discarded %s cited meeting id(s) that were not in the retrieved context.",
            len(invented),
        )
    return valid


def _build_sources(
    passages: Sequence[RetrievedPassage], cited: Optional[Sequence[str]]
) -> List[SourceReference]:
    """One source entry per meeting, strongest passage first.

    When the model named the meetings it used, those lead the list; the rest are
    still returned so the user can check what else was considered.
    """
    best: Dict[str, RetrievedPassage] = {}
    for passage in passages:
        current = best.get(passage.meeting_id)
        if current is None or passage.score > current.score:
            best[passage.meeting_id] = passage

    references = [
        SourceReference(
            meeting_id=passage.meeting_id,
            meeting_title=passage.meeting_title,
            meeting_date=passage.meeting_date,
            source_type=passage.source_type,
            excerpt=truncate(passage.content, 400),
            score=passage.score,
        )
        for passage in sorted(best.values(), key=lambda p: p.score, reverse=True)
    ]

    if not cited:
        return references

    cited_set = set(cited)
    return sorted(references, key=lambda ref: ref.meeting_id not in cited_set)
