"""Milestone 3 end-to-end - the complete scenario, wired together.

Runs the real services against fake external systems:

    Supabase rows (fake)  ->  KnowledgeRepository  ->  build_documents
      ->  EmbeddingService -> FastEmbedProvider (bag-of-words model in place of ONNX)
      ->  VectorRepository (Pinecone HTTP mocked by respx, in-memory index)
      ->  SemanticSearchService  ->  RAGService  ->  LLMService -> Groq (stub client)

The only fakes are the things that would cost money, need credentials, or need
a 67 MB model download: the database driver, Pinecone's HTTP, the ONNX model
and Groq. Every service, schema, prompt and piece of retrieval logic in between
is the production code.

The bag-of-words model is crude on purpose - texts that share words land closer
together - which is enough to prove the pipeline retrieves the right meeting.
The real model's *semantic* matching is covered by the opt-in test in
``test_knowledge.py`` and by the live verification run.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List

import httpx
import pytest
import respx

from app.ai.llm_service import LLMService
from app.knowledge.documents import build_documents
from app.models.enums import IndexStatus
from app.repositories.vector_repository import VectorRepository, reset_host_cache
from app.services.embedding_service import EmbeddingService
from app.services.knowledge_index_service import KnowledgeIndexService
from app.services.rag_service import RAGService
from app.services.semantic_search_service import SemanticSearchService
from tests.helpers import BagOfWordsModel, StubGroqClient, fake_embedding_provider

INDEX_HOST = "meeting-knowledge-e2e.svc.aped.pinecone.io"

MIGRATION_MEETING = "11111111-aaaa-bbbb-cccc-000000000001"
MOBILE_MEETING = "22222222-aaaa-bbbb-cccc-000000000002"

#: Two historical meetings, as they would already exist in Supabase.
MEETINGS: Dict[str, Dict[str, Any]] = {
    MIGRATION_MEETING: {
        "id": MIGRATION_MEETING,
        "title": "Platform Infrastructure Sync",
        "created_at": "2026-09-10T09:00:00+00:00",
        "status": "COMPLETED",
        "transcript_text": (
            "Arjun raised the database migration. The team agreed the Postgres "
            "schema move has to be finished before anything else ships. "
            "Sneha will write the migration plan."
        ),
    },
    MOBILE_MEETING: {
        "id": MOBILE_MEETING,
        "title": "Mobile Application Planning",
        "created_at": "2026-09-15T10:00:00+00:00",
        "status": "COMPLETED",
        "transcript_text": (
            "Ravi opened the meeting about the mobile application launch. "
            "The team decided the mobile application must be released by Friday. "
            "Priya will run the final UI testing."
        ),
    },
}

INTELLIGENCE: Dict[str, Dict[str, Any]] = {
    MIGRATION_MEETING: {
        "summary": "The team planned the Postgres database migration.",
        "key_points": [{"id": "kp-a", "text": "The database migration blocks every other release"}],
        "decisions": [
            {"id": "dec-a", "text": "Finish the database migration before any new release",
             "context": "Agreed unanimously."}
        ],
        "action_items": [
            {"id": "ai-a", "task": "Write the database migration plan", "assigned_to": "Sneha",
             "deadline": "next Wednesday", "priority": "high", "status": "pending", "context": None}
        ],
        "participants": [{"id": "p-a", "name": "Arjun", "role": "Platform"},
                         {"id": "p-b", "name": "Sneha", "role": None}],
    },
    MOBILE_MEETING: {
        "summary": "The team planned the mobile application launch.",
        "key_points": [{"id": "kp-b", "text": "The mobile application launch is on track"}],
        "decisions": [
            {"id": "dec-b", "text": "Release the mobile application by Friday",
             "context": "Everyone agreed on the Friday deadline."}
        ],
        "action_items": [
            {"id": "ai-b", "task": "Run the final UI testing for the mobile application",
             "assigned_to": "Priya", "deadline": "Friday", "priority": "high",
             "status": "pending", "context": None}
        ],
        "participants": [{"id": "p-c", "name": "Ravi", "role": "Backend"},
                         {"id": "p-d", "name": "Priya", "role": "QA"}],
    },
}


# ------------------------------------------------------- fake external world
class FakeMeetingRepo:
    def get_or_404(self, meeting_id: str) -> Dict[str, Any]:
        return MEETINGS[meeting_id]


class FakeIntelligenceRepo:
    def get_intelligence(self, meeting_id: str):
        return INTELLIGENCE.get(meeting_id)


class FakeKnowledgeRepo:
    """The real KnowledgeRepository's shape, backed by the dicts above."""

    def __init__(self) -> None:
        from app.repositories.knowledge_repository import KnowledgeRepository

        self._inner = KnowledgeRepository(FakeMeetingRepo(), FakeIntelligenceRepo())
        self.state: Dict[str, Dict[str, Any]] = {}
        self.tracks_index_status = True

    def get_meeting_bundle(self, meeting_id):
        return self._inner.get_meeting_bundle(meeting_id)

    def get_meeting_headers(self, meeting_ids):
        return {mid: MEETINGS[mid] for mid in meeting_ids if mid in MEETINGS}

    def list_indexable_meetings(self, *, limit=200):
        return [{"id": mid} for mid in MEETINGS]

    def get_index_state(self, meeting_id):
        return self.state.get(meeting_id, {})

    def mark_index_status(self, meeting_id, status, *, fingerprint=None, error=None):
        self.state[meeting_id] = {
            "index_status": status.value,
            "knowledge_fingerprint": fingerprint,
        }

    def index_summary(self, *, signature=None):
        counts: Dict[str, int] = {}
        for entry in self.state.values():
            key = entry["index_status"]
            if signature and key == "INDEXED" and not (
                entry.get("knowledge_fingerprint") or ""
            ).startswith(f"{signature}:"):
                key = "STALE"
            counts[key] = counts.get(key, 0) + 1
        return counts


class FakePinecone:
    """An in-memory vector index that behaves like the parts we rely on:
    deterministic-id upsert, prefix listing, delete-by-id, filtered cosine query."""

    def __init__(self) -> None:
        self.vectors: Dict[str, Dict[str, Any]] = {}
        self.upsert_calls = 0
        self.query_calls = 0
        self.last_filter: Any = None

    def install(self, mock: respx.MockRouter) -> None:
        mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(side_effect=self._upsert)
        mock.post(f"https://{INDEX_HOST}/vectors/delete").mock(side_effect=self._delete)
        mock.post(f"https://{INDEX_HOST}/query").mock(side_effect=self._query)
        mock.get(f"https://{INDEX_HOST}/vectors/list").mock(side_effect=self._list)

    def _upsert(self, request: httpx.Request) -> httpx.Response:
        self.upsert_calls += 1
        body = json.loads(request.content)
        for vector in body["vectors"]:
            self.vectors[vector["id"]] = vector
        return httpx.Response(200, json={"upsertedCount": len(body["vectors"])})

    def _delete(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "deleteAll" not in body, "the application must never wipe the index"
        for vector_id in body.get("ids", []):
            self.vectors.pop(vector_id, None)
        return httpx.Response(200, json={})

    def _list(self, request: httpx.Request) -> httpx.Response:
        prefix = request.url.params.get("prefix", "")
        ids = [{"id": vid} for vid in self.vectors if vid.startswith(prefix)]
        return httpx.Response(200, json={"vectors": ids, "pagination": {}})

    def _query(self, request: httpx.Request) -> httpx.Response:
        self.query_calls += 1
        body = json.loads(request.content)
        self.last_filter = body.get("filter")
        scored = [
            {"id": v["id"], "score": _cosine(body["vector"], v["values"]), "metadata": v["metadata"]}
            for v in self.vectors.values()
            if _matches_filter(v["metadata"], self.last_filter)
        ]
        scored.sort(key=lambda item: item["score"], reverse=True)
        return httpx.Response(200, json={"matches": scored[: body.get("topK", 10)]})


def _matches_filter(metadata: Dict[str, Any], clause: Any) -> bool:
    """Enough of Pinecone's filter language for the cases used here."""
    if not clause:
        return True
    if "$and" in clause:
        return all(_matches_filter(metadata, part) for part in clause["$and"])
    for field, condition in clause.items():
        value = metadata.get(field)
        for operator, expected in condition.items():
            if operator == "$eq" and value != expected:
                return False
            if operator == "$gte" and (value is None or value < expected):
                return False
            if operator == "$lte" and (value is None or value > expected):
                return False
    return True


def _cosine(left: List[float], right: List[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left)) or 1.0
    norm_right = math.sqrt(sum(b * b for b in right)) or 1.0
    return dot / (norm_left * norm_right)


def build_stack(knowledge: FakeKnowledgeRepo, model: BagOfWordsModel = None):
    """Real services, pointed at the fakes."""
    embeddings = EmbeddingService(fake_embedding_provider(model=model))
    vectors = VectorRepository(
        api_key="test-pinecone-key", index_name="meeting-knowledge",
        namespace="meetings", host=INDEX_HOST,
    )
    indexer = KnowledgeIndexService(knowledge, embeddings, vectors)
    search = SemanticSearchService(embeddings, vectors, knowledge)
    return indexer, search


def groq_answer(answer: str, meeting_ids: List[str], found: bool = True) -> str:
    return json.dumps({"answer": answer, "answer_found": found,
                       "used_meeting_ids": meeting_ids, "confidence": "high"})


def rag(search: SemanticSearchService, groq: StubGroqClient) -> RAGService:
    return RAGService(search=search, llm=LLMService(groq, schema_retry_attempts=1))


@pytest.fixture(autouse=True)
def _reset():
    reset_host_cache()
    yield
    reset_host_cache()


# =============================================================== the scenario
@pytest.mark.asyncio
class TestMilestone3EndToEnd:
    async def test_historical_meetings_become_searchable_and_answerable(self):
        """The full walkthrough: index -> semantic search -> grounded Groq answer."""
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)

            # --- index the meetings that already exist in the database -------
            summary = await indexer.index_all()
            assert (summary.processed, summary.indexed, summary.failed) == (2, 2, 0)
            assert pinecone.vectors, "vectors must actually be stored"

            stored_types = {v["metadata"]["source_type"] for v in pinecone.vectors.values()}
            assert {"transcript", "summary", "decision", "action_item",
                    "key_point", "participants"} <= stored_types

            for vector in pinecone.vectors.values():
                assert vector["metadata"]["meeting_id"] in MEETINGS
                assert vector["id"].startswith(vector["metadata"]["meeting_id"] + "#")
                assert vector["metadata"]["embedding_model"] == "test/bag-of-words"

            # --- semantic search finds the right historical meeting ----------
            response = await search.search("Which meeting discussed the database migration?")
            assert response.results[0].meeting_id == MIGRATION_MEETING
            assert response.results[0].meeting_title == "Platform Infrastructure Sync"
            assert response.results[0].excerpt

            # --- the RAG question, answered by Groq from the retrieved context
            groq = StubGroqClient([groq_answer(
                "The team decided to release the mobile application by Friday.",
                [MOBILE_MEETING])])
            answer = await rag(search, groq).ask(
                "What deadline was decided for the mobile application?"
            )

        assert answer.answer_found is True
        assert "Friday" in answer.answer
        assert answer.provider == "groq"
        assert groq.calls == 1
        assert any(source.meeting_id == MOBILE_MEETING for source in answer.sources)

    async def test_groq_is_given_the_real_deadline_not_asked_to_guess(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        groq = StubGroqClient([groq_answer("Friday.", [MOBILE_MEETING])])

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)
            await indexer.index_all()
            await rag(search, groq).ask("What deadline was decided for the mobile application?")

        assert "Friday" in groq.prompts[0], "the deadline must come from the records"
        assert "MEETING_ID:" in groq.prompts[0]

    async def test_search_itself_never_calls_groq(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)
            await indexer.index_all()
            response = await search.search("database migration")
        assert response.results
        assert pinecone.query_calls == 1, "one vector query per search"

    async def test_query_and_documents_use_the_same_model(self):
        model = BagOfWordsModel(fake_embedding_provider().dimensions)
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge, model)
            await indexer.index_all()
            calls_after_indexing = model.embed_calls
            await search.search("database migration")
        assert model.embed_calls == calls_after_indexing + 1
        assert {"embedding_model": {"$eq": "test/bag-of-words"}} in (
            pinecone.last_filter["$and"] if "$and" in pinecone.last_filter
            else [pinecone.last_filter]
        )

    async def test_re_indexing_unchanged_meetings_embeds_nothing(self):
        model = BagOfWordsModel(fake_embedding_provider().dimensions)
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, _ = build_stack(knowledge, model)
            await indexer.index_all()
            after_first = model.embed_calls
            assert after_first > 0
            second = await indexer.index_all()

        assert second.skipped_unchanged == 2 and second.indexed == 0
        assert model.embed_calls == after_first, "no second round of embeddings"

    async def test_editing_a_meeting_refreshes_only_that_meeting(self):
        model = BagOfWordsModel(fake_embedding_provider().dimensions)
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, _ = build_stack(knowledge, model)
            await indexer.index_all()
            baseline = model.embed_calls

            original = INTELLIGENCE[MOBILE_MEETING]["summary"]
            INTELLIGENCE[MOBILE_MEETING]["summary"] = "A completely rewritten summary."
            try:
                result = await indexer.index_meeting(MOBILE_MEETING)
                unchanged = await indexer.index_meeting(MIGRATION_MEETING)
            finally:
                INTELLIGENCE[MOBILE_MEETING]["summary"] = original

        assert result.status is IndexStatus.INDEXED and result.skipped_unchanged is False
        assert unchanged.skipped_unchanged is True, "the other meeting was not touched"
        assert model.embed_calls == baseline + 1

    async def test_deleting_a_meeting_removes_only_its_vectors(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, _ = build_stack(knowledge)
            await indexer.index_all()
            before = len(pinecone.vectors)
            mobile_vectors = sum(1 for vid in pinecone.vectors if vid.startswith(MOBILE_MEETING))
            removed = await indexer.delete_meeting(MOBILE_MEETING)

        assert removed == mobile_vectors
        assert len(pinecone.vectors) == before - mobile_vectors
        assert not any(vid.startswith(MOBILE_MEETING) for vid in pinecone.vectors)
        assert any(vid.startswith(MIGRATION_MEETING) for vid in pinecone.vectors)

    async def test_metadata_filtering_narrows_the_search(self):
        from app.schemas.search import SearchFilters

        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)
            await indexer.index_all()
            scoped = await search.search(
                "what was decided", filters=SearchFilters(meeting_id=MIGRATION_MEETING)
            )
            decisions = await search.search(
                "what was decided", filters=SearchFilters(source_type="decision")
            )

        assert scoped.results
        assert all(r.meeting_id == MIGRATION_MEETING for r in scoped.results)
        for result in decisions.results:
            assert result.matched_source_types == ["decision"]

    async def test_a_question_with_no_matching_records_is_answered_honestly(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        groq = StubGroqClient(["should never be called"])

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)
            await indexer.index_all()

            from app.config import get_settings

            settings = get_settings()
            original = settings.search_min_score
            object.__setattr__(settings, "search_min_score", 0.99)
            try:
                answer = await rag(search, groq).ask("What is the office wifi password?")
            finally:
                object.__setattr__(settings, "search_min_score", original)

        assert answer.answer_found is False
        assert "couldn't find" in answer.answer.lower()
        assert answer.sources == []
        assert groq.calls == 0, "no Groq request when there is nothing to ground an answer in"

    async def test_multiple_meetings_can_answer_one_question(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        groq = StubGroqClient([groq_answer("Two meetings are relevant.",
                                           [MIGRATION_MEETING, MOBILE_MEETING])])

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, search = build_stack(knowledge)
            await indexer.index_all()
            answer = await rag(search, groq).ask(
                "What did the team decide about releases and migrations?", top_k=20
            )

        assert f"MEETING_ID: {MIGRATION_MEETING}" in groq.prompts[0]
        assert f"MEETING_ID: {MOBILE_MEETING}" in groq.prompts[0]
        assert answer.searched_meetings == 2
        assert len(answer.sources) == 2

    async def test_existing_meeting_records_are_never_modified_by_indexing(self):
        """Milestone 1 and 2 data is read-only as far as Milestone 3 is concerned."""
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        before = json.dumps(MEETINGS, sort_keys=True), json.dumps(INTELLIGENCE, sort_keys=True)

        with respx.mock as mock:
            pinecone.install(mock)
            indexer, _ = build_stack(knowledge)
            await indexer.index_all()

        after = json.dumps(MEETINGS, sort_keys=True), json.dumps(INTELLIGENCE, sort_keys=True)
        assert before == after

    async def test_status_flags_meetings_indexed_by_another_model_as_stale(self):
        pinecone, knowledge = FakePinecone(), FakeKnowledgeRepo()
        with respx.mock as mock:
            pinecone.install(mock)
            indexer, _ = build_stack(knowledge)
            await indexer.index_all()
        knowledge.state[MOBILE_MEETING]["knowledge_fingerprint"] = "old-model@768:abc"

        counts = indexer.status()["counts"]
        assert counts == {"INDEXED": 1, "STALE": 1}

    async def test_documents_built_for_indexing_match_what_is_stored(self):
        """Nothing is invented between the database and the vector index."""
        bundle = FakeKnowledgeRepo().get_meeting_bundle(MOBILE_MEETING)
        combined = " ".join(document.content for document in build_documents(bundle))

        assert "Friday" in combined and "Priya" in combined
        assert "mobile application" in combined.lower()
        assert "Saturday" not in combined
