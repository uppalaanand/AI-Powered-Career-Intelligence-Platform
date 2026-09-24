"""Milestone 3, Tasks 3 & 4 - vector database CRUD and semantic search.

Pinecone is mocked at the HTTP layer with ``respx``, so these tests exercise the
**real** request bodies and response parsing without a Pinecone account, an API
key, or a single billable operation. That matters twice over: the project runs
on free tiers, and a test suite that needed credentials would not be runnable by
the person marking it.

Covered here:
  Task 3  insert / update / delete / similarity search / metadata filtering /
          meeting-to-vector mapping / dimension safety
  Task 4  the full search pipeline, grouping, ranking, and the rule that search
          never calls an LLM
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest
import respx

from app.config import get_settings
from app.knowledge.documents import KnowledgeDocument, build_documents
from app.models.enums import IndexStatus
from app.repositories.vector_repository import (
    VectorMatch,
    VectorRecord,
    VectorRepository,
    reset_host_cache,
)
from app.schemas.search import SearchFilters
from app.services.knowledge_index_service import KnowledgeIndexService
from app.services.semantic_search_service import (
    SemanticSearchService,
    build_metadata_filter,
    scope_to_model,
)
from app.utils.errors import (
    SearchUnavailableError,
    VectorDimensionMismatchError,
    VectorStoreError,
    VectorStoreNotConfiguredError,
)
from tests.test_knowledge import MEETING_ID, make_bundle

INDEX_HOST = "meeting-knowledge-abc123.svc.aped.pinecone.io"
#: Follows configuration (384 for the default local model), because the
#: repository refuses vectors whose size differs from EMBEDDING_DIMENSIONS.
DIMENSIONS = get_settings().embedding_dimensions


@pytest.fixture(autouse=True)
def _clear_host_cache():
    """The data-plane host is cached per process; isolate every test."""
    reset_host_cache()
    yield
    reset_host_cache()


def repo(**kwargs: Any) -> VectorRepository:
    """A repository pinned to a known host, so no control-plane call happens."""
    return VectorRepository(
        api_key="test-pinecone-key",
        index_name="meeting-knowledge",
        namespace="meetings",
        host=INDEX_HOST,
        **kwargs,
    )


def record(vector_id: str, meeting_id: str = MEETING_ID, **metadata: Any) -> VectorRecord:
    return VectorRecord(
        id=vector_id,
        values=[0.01] * DIMENSIONS,
        metadata={"meeting_id": meeting_id, "source_type": "decision", **metadata},
    )


def match(
    vector_id: str,
    score: float,
    meeting_id: str = MEETING_ID,
    source_type: str = "transcript",
    content: str = "We discussed the database migration.",
) -> Dict[str, Any]:
    return {
        "id": vector_id,
        "score": score,
        "metadata": {
            "meeting_id": meeting_id,
            "source_type": source_type,
            "source_id": "transcript",
            "chunk_index": 0,
            "content": content,
            "meeting_title": "Mobile Application Planning",
        },
    }


class FakeEmbeddings:
    """Stands in for the embedding service. Counts its calls."""

    def __init__(self, dimensions: int = DIMENSIONS, configured: bool = True) -> None:
        self.dimensions = dimensions
        self.is_configured = configured
        self.provider_name = "fake"
        self.model = "fake-embed"
        self.query_calls = 0
        self.document_calls = 0

    @property
    def signature(self) -> str:
        return f"{self.model}@{self.dimensions}"

    async def embed_query(self, query: str) -> List[float]:
        self.query_calls += 1
        return [0.02] * self.dimensions

    async def embed_documents(self, documents):
        self.document_calls += 1
        return [(document, [0.02] * self.dimensions) for document in documents]


class FakeKnowledge:
    """Supabase stand-in. Records how many reads search performs."""

    def __init__(self, meetings: Dict[str, Dict[str, Any]]) -> None:
        self._meetings = meetings
        self.header_calls = 0
        self.statuses: List[tuple] = []
        self.state: Dict[str, Dict[str, Any]] = {}
        self.tracks_index_status = True

    def get_meeting_headers(self, meeting_ids):
        self.header_calls += 1
        return {mid: self._meetings[mid] for mid in meeting_ids if mid in self._meetings}

    def get_meeting_bundle(self, meeting_id):
        return make_bundle()

    def get_index_state(self, meeting_id):
        return self.state.get(meeting_id, {})

    def mark_index_status(self, meeting_id, status, *, fingerprint=None, error=None):
        self.statuses.append((meeting_id, status))
        self.state[meeting_id] = {
            "index_status": status.value,
            "knowledge_fingerprint": fingerprint,
        }

    def list_indexable_meetings(self, *, limit=200):
        return [{"id": mid} for mid in self._meetings]

    def index_summary(self):
        return {}


MEETINGS = {
    MEETING_ID: {
        "id": MEETING_ID,
        "title": "Mobile Application Planning",
        "created_at": "2026-09-15T10:00:00+00:00",
        "status": "COMPLETED",
    },
    "meeting-b": {
        "id": "meeting-b",
        "title": "Database Migration Review",
        "created_at": "2026-09-18T10:00:00+00:00",
        "status": "COMPLETED",
    },
}


# ============================================================ TEST 3: insert
@pytest.mark.asyncio
class TestVectorInsert:
    async def test_vectors_are_upserted_with_ids_values_and_metadata(self):
        captured: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"upsertedCount": 1})

        with respx.mock(assert_all_called=True) as mock:
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(side_effect=capture)
            written = await repo().upsert([record(f"{MEETING_ID}#decision#dec-1#0")])

        assert written == 1
        assert captured["namespace"] == "meetings"
        stored = captured["vectors"][0]
        assert stored["id"] == f"{MEETING_ID}#decision#dec-1#0"
        assert len(stored["values"]) == DIMENSIONS
        assert stored["metadata"]["meeting_id"] == MEETING_ID

    async def test_the_api_key_travels_as_a_header(self):
        seen: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen["key"] = request.headers.get("Api-Key")
            seen["url"] = str(request.url)
            return httpx.Response(200, json={})

        with respx.mock:
            respx.post(f"https://{INDEX_HOST}/vectors/upsert").mock(side_effect=capture)
            await repo().upsert([record("v1")])

        assert seen["key"] == "test-pinecone-key"
        assert "test-pinecone-key" not in seen["url"]

    async def test_large_sets_are_batched_rather_than_sent_as_one_body(self):
        records = [record(f"{MEETING_ID}#transcript#transcript#{i}") for i in range(200)]
        with respx.mock as mock:
            route = mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            written = await repo().upsert(records)

        assert written == 200
        assert route.call_count > 1

    async def test_upserting_nothing_makes_no_request(self):
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"https://{INDEX_HOST}/vectors/upsert")
            assert await repo().upsert([]) == 0
        assert route.call_count == 0

    async def test_a_wrong_sized_vector_is_caught_before_it_is_sent(self):
        bad = VectorRecord(id="v1", values=[0.1] * 100, metadata={"meeting_id": MEETING_ID})
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"https://{INDEX_HOST}/vectors/upsert")
            with pytest.raises(VectorDimensionMismatchError) as exc:
                await repo().upsert([bad])
        assert route.call_count == 0, "a malformed vector must never reach the index"
        assert exc.value.details["expected"] == DIMENSIONS

    async def test_no_request_is_made_without_an_api_key(self):
        store = VectorRepository(api_key=None, host=INDEX_HOST)
        store._api_key = None
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(url__startswith=f"https://{INDEX_HOST}")
            with pytest.raises(VectorStoreNotConfiguredError):
                await store.upsert([record("v1")])
        assert route.call_count == 0


# ============================================================ TEST 4: update
@pytest.mark.asyncio
class TestVectorUpdate:
    async def test_reindexing_reuses_the_same_ids_instead_of_duplicating(self):
        """Same source content -> same vector ids -> upsert overwrites."""
        first = [d.vector_id for d in build_documents(make_bundle())]
        edited = make_bundle()
        edited["decisions"] = [
            {"id": "dec-1", "text": "Complete the launch by NEXT Friday", "context": "Changed."}
        ]
        second = [d.vector_id for d in build_documents(edited)]

        assert first == second, "editing content must not create new vector ids"

    async def test_changed_content_is_re_embedded_and_upserted(self):
        knowledge = FakeKnowledge(MEETINGS)
        embeddings = FakeEmbeddings()
        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            upsert = mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            service = KnowledgeIndexService(knowledge, embeddings, repo())
            result = await service.index_meeting(MEETING_ID)

        assert result.status is IndexStatus.INDEXED
        assert result.vectors_written > 0
        assert upsert.called
        assert embeddings.document_calls == 1

    async def test_every_written_vector_records_the_embedding_model(self):
        knowledge = FakeKnowledge(MEETINGS)
        written: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            written.update(json.loads(request.content))
            return httpx.Response(200, json={})

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(side_effect=capture)
            await KnowledgeIndexService(knowledge, FakeEmbeddings(), repo()).index_meeting(
                MEETING_ID
            )

        assert written["vectors"]
        for vector in written["vectors"]:
            assert vector["metadata"]["embedding_model"] == "fake-embed"

    async def test_a_model_change_forces_re_embedding_of_unchanged_content(self):
        knowledge = FakeKnowledge(MEETINGS)
        old_model, new_model = FakeEmbeddings(), FakeEmbeddings()
        new_model.model = "a-newer-model"

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            await KnowledgeIndexService(knowledge, old_model, repo()).index_meeting(MEETING_ID)
            result = await KnowledgeIndexService(knowledge, new_model, repo()).index_meeting(
                MEETING_ID
            )

        assert result.skipped_unchanged is False
        assert new_model.document_calls == 1

    async def test_unchanged_content_costs_no_embedding_request(self):
        """The quota rule: re-indexing identical content must not call the API."""
        knowledge = FakeKnowledge(MEETINGS)
        embeddings = FakeEmbeddings()
        store = repo()

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            service = KnowledgeIndexService(knowledge, embeddings, store)
            await service.index_meeting(MEETING_ID)
            calls_after_first = embeddings.document_calls

            second = await service.index_meeting(MEETING_ID)

        assert second.skipped_unchanged is True
        assert second.status is IndexStatus.INDEXED
        assert embeddings.document_calls == calls_after_first, "no re-embedding"

    async def test_force_re_embeds_even_when_unchanged(self):
        knowledge = FakeKnowledge(MEETINGS)
        embeddings = FakeEmbeddings()
        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            service = KnowledgeIndexService(knowledge, embeddings, repo())
            await service.index_meeting(MEETING_ID)
            await service.index_meeting(MEETING_ID, force=True)

        assert embeddings.document_calls == 2

    async def test_vectors_whose_source_disappeared_are_removed(self):
        """A decision deleted in Supabase must not linger in search results."""
        knowledge = FakeKnowledge(MEETINGS)
        stale_id = f"{MEETING_ID}#decision#deleted-decision#0"
        deleted: Dict[str, Any] = {}

        def capture_delete(request: httpx.Request) -> httpx.Response:
            deleted.update(json.loads(request.content))
            return httpx.Response(200, json={})

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(
                    200, json={"vectors": [{"id": stale_id}], "pagination": {}}
                )
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/delete").mock(side_effect=capture_delete)

            service = KnowledgeIndexService(knowledge, FakeEmbeddings(), repo())
            result = await service.index_meeting(MEETING_ID)

        assert result.vectors_removed == 1
        assert deleted["ids"] == [stale_id]


# ============================================================ TEST 5: delete
@pytest.mark.asyncio
class TestVectorDelete:
    async def test_deleting_a_meeting_removes_only_its_own_vectors(self):
        captured: Dict[str, Any] = {}
        listed: Dict[str, Any] = {}

        def capture_list(request: httpx.Request) -> httpx.Response:
            listed.update(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "vectors": [
                        {"id": f"{MEETING_ID}#transcript#transcript#0"},
                        {"id": f"{MEETING_ID}#decision#dec-1#0"},
                    ],
                    "pagination": {},
                },
            )

        def capture_delete(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={})

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(side_effect=capture_list)
            mock.post(f"https://{INDEX_HOST}/vectors/delete").mock(side_effect=capture_delete)
            removed = await repo().delete_by_meeting(MEETING_ID)

        assert removed == 2
        assert listed["prefix"] == f"{MEETING_ID}#", "scoped by meeting prefix"
        assert all(vid.startswith(f"{MEETING_ID}#") for vid in captured["ids"])
        assert "deleteAll" not in captured

    async def test_pagination_is_followed_so_nothing_is_left_behind(self):
        pages = [
            {"vectors": [{"id": f"{MEETING_ID}#transcript#transcript#0"}],
             "pagination": {"next": "token-2"}},
            {"vectors": [{"id": f"{MEETING_ID}#transcript#transcript#1"}], "pagination": {}},
        ]
        calls = {"n": 0}

        def paged(request: httpx.Request) -> httpx.Response:
            page = pages[calls["n"]]
            calls["n"] += 1
            return httpx.Response(200, json=page)

        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(side_effect=paged)
            ids = await repo().list_ids_for_meeting(MEETING_ID)

        assert len(ids) == 2
        assert calls["n"] == 2

    async def test_deleting_a_meeting_with_no_vectors_is_a_safe_no_op(self):
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            delete = mock.post(f"https://{INDEX_HOST}/vectors/delete")
            removed = await repo().delete_by_meeting(MEETING_ID)

        assert removed == 0
        assert delete.call_count == 0

    async def test_there_is_no_delete_everything_operation(self):
        """Guard rail: no ordinary application action can wipe the index."""
        assert not hasattr(VectorRepository, "delete_all")
        assert not hasattr(VectorRepository, "clear")

    async def test_delete_failure_does_not_stop_a_meeting_being_deleted(self):
        knowledge = FakeKnowledge(MEETINGS)
        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(500, json={"message": "boom"})
            )
            service = KnowledgeIndexService(knowledge, FakeEmbeddings(), repo())
            removed = await service.delete_meeting_safely(MEETING_ID)
        assert removed == 0  # reported, not raised


# ==================================== TEST 6 & 7: similarity search + filters
@pytest.mark.asyncio
class TestSimilaritySearch:
    async def test_query_sends_the_vector_and_parses_the_matches(self):
        captured: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"matches": [match("v1", 0.91)]})

        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(side_effect=capture)
            matches = await repo().query([0.02] * DIMENSIONS, top_k=5)

        assert captured["topK"] == 5
        assert captured["namespace"] == "meetings"
        assert captured["includeMetadata"] is True
        assert len(matches) == 1
        assert isinstance(matches[0], VectorMatch)
        assert matches[0].meeting_id == MEETING_ID
        assert matches[0].score == pytest.approx(0.91)

    async def test_an_empty_query_vector_is_refused(self):
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"https://{INDEX_HOST}/query")
            with pytest.raises(VectorStoreError):
                await repo().query([])
        assert route.call_count == 0

    async def test_the_filter_is_applied_inside_the_vector_query(self):
        captured: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"matches": []})

        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(side_effect=capture)
            await repo().query(
                [0.02] * DIMENSIONS,
                top_k=5,
                metadata_filter={"source_type": {"$eq": "decision"}},
            )

        assert captured["filter"] == {"source_type": {"$eq": "decision"}}



# ========================================================== TEST 7: filters
class TestMetadataFilterTranslation:
    """Pure translation of API filters into the index's filter language."""

    def test_model_scope_is_added_to_an_empty_filter(self):
        assert scope_to_model(None, "m") == {"embedding_model": {"$eq": "m"}}

    def test_model_scope_joins_an_existing_filter(self):
        scoped = scope_to_model({"meeting_id": {"$eq": "x"}}, "m")
        assert scoped == {"$and": [{"meeting_id": {"$eq": "x"}},
                                   {"embedding_model": {"$eq": "m"}}]}

    def test_model_scope_extends_an_existing_and(self):
        scoped = scope_to_model({"$and": [{"a": 1}, {"b": 2}]}, "m")
        assert scoped["$and"][-1] == {"embedding_model": {"$eq": "m"}}
        assert len(scoped["$and"]) == 3

    def test_meeting_filter_is_translated_for_the_index(self):
        built = build_metadata_filter(SearchFilters(meeting_id=MEETING_ID))
        assert built == {"meeting_id": {"$eq": MEETING_ID}}

    def test_source_type_filter_is_translated(self):
        assert build_metadata_filter(SearchFilters(source_type="decision")) == {
            "source_type": {"$eq": "decision"}
        }

    def test_participant_filter_targets_the_assignee(self):
        assert build_metadata_filter(SearchFilters(participant="Ravi")) == {
            "assigned_to": {"$eq": "Ravi"}
        }

    def test_date_range_becomes_a_numeric_range(self):
        built = build_metadata_filter(
            SearchFilters(date_from="2026-09-01", date_to="2026-09-30")
        )
        assert "$gte" in built["meeting_date_ts"] and "$lte" in built["meeting_date_ts"]
        assert built["meeting_date_ts"]["$gte"] < built["meeting_date_ts"]["$lte"]

    def test_several_filters_combine_with_and(self):
        built = build_metadata_filter(
            SearchFilters(meeting_id=MEETING_ID, source_type="action_item")
        )
        assert "$and" in built and len(built["$and"]) == 2

    def test_no_filters_means_no_filter_clause(self):
        assert build_metadata_filter(None) is None
        assert build_metadata_filter(SearchFilters()) is None

    def test_an_unknown_source_type_is_rejected_at_the_edge(self):
        with pytest.raises(Exception):
            SearchFilters(source_type="not_a_real_type")

# =========================================================== TEST 9: Task 4
@pytest.mark.asyncio
class TestSemanticSearch:
    def _service(self, embeddings=None, knowledge=None):
        return SemanticSearchService(
            embeddings or FakeEmbeddings(),
            repo(),
            knowledge or FakeKnowledge(MEETINGS),
        )

    async def test_a_natural_language_query_returns_relevant_meetings(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "matches": [
                            match("v1", 0.93, meeting_id="meeting-b",
                                  content="We planned the database migration for next sprint."),
                            match("v2", 0.71, meeting_id=MEETING_ID,
                                  content="The launch depends on the migration."),
                        ]
                    },
                )
            )
            response = await self._service().search(
                "Which meeting discussed the database migration?"
            )

        assert len(response.results) == 2
        assert response.results[0].meeting_id == "meeting-b"
        assert response.results[0].meeting_title == "Database Migration Review"
        assert response.results[0].score > response.results[1].score
        assert "database migration" in response.results[0].excerpt

    async def test_results_carry_everything_needed_to_render_them(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(200, json={"matches": [match("v1", 0.88)]})
            )
            response = await self._service().search("database migration")

        result = response.results[0]
        assert result.meeting_id and result.meeting_title
        assert result.meeting_date and result.excerpt
        assert result.matched_source_types == ["transcript"]
        assert 0 <= result.score <= 1
        assert response.took_ms >= 0

    async def test_search_costs_one_embedding_one_query_and_one_db_read(self):
        """The three-second budget depends on this staying constant."""
        embeddings = FakeEmbeddings()
        knowledge = FakeKnowledge(MEETINGS)
        with respx.mock as mock:
            route = mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200,
                    json={"matches": [match(f"v{i}", 0.9 - i / 100) for i in range(8)]},
                )
            )
            await self._service(embeddings, knowledge).search("database migration")

        assert embeddings.query_calls == 1
        assert route.call_count == 1
        assert knowledge.header_calls == 1, "meetings must be resolved in one batched read"

    async def test_search_never_calls_an_llm(self):
        """Finding meetings is a vector problem; Groq is for answers only."""
        import inspect

        from app.services import semantic_search_service

        source = inspect.getsource(semantic_search_service)
        for forbidden in ("LLMService", "llm_service", "GroqClient", "groq_client",
                          "generate_validated", "complete_json"):
            assert forbidden not in source

    async def test_every_query_is_scoped_to_the_current_embedding_model(self):
        """Vectors from a different model must never be compared with the query."""
        captured: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"matches": []})

        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(side_effect=capture)
            await self._service().search(
                "launch", filters=SearchFilters(source_type="decision")
            )

        clauses = captured["filter"]["$and"]
        assert {"embedding_model": {"$eq": "fake-embed"}} in clauses
        assert {"source_type": {"$eq": "decision"}} in clauses

    async def test_passages_from_one_meeting_collapse_into_one_result(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "matches": [
                            match("v1", 0.70, source_type="transcript", content="First passage."),
                            match("v2", 0.95, source_type="decision", content="Best passage."),
                            match("v3", 0.60, source_type="summary", content="Third passage."),
                        ]
                    },
                )
            )
            response = await self._service().search("launch")

        assert len(response.results) == 1, "one meeting is one result"
        result = response.results[0]
        assert len(result.matches) == 3
        assert result.score == pytest.approx(0.95)
        assert result.excerpt == "Best passage.", "the strongest passage is shown"
        assert set(result.matched_source_types) == {"transcript", "decision", "summary"}
        assert response.total_matches == 3

    async def test_no_results_returns_a_clear_message_not_an_error(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(200, json={"matches": []})
            )
            response = await self._service().search("something nobody ever discussed")

        assert response.results == []
        assert "No relevant meetings" in response.message

    async def test_a_vector_store_failure_becomes_a_friendly_error(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(500, json={"message": "internal"})
            )
            with pytest.raises(SearchUnavailableError) as exc:
                await self._service().search("database migration")

        assert exc.value.code == "SEARCH_UNAVAILABLE"
        assert "Unable to search" in exc.value.message
        assert "internal" not in exc.value.message

    async def test_an_empty_query_is_rejected_before_any_call(self):
        embeddings = FakeEmbeddings()
        with respx.mock(assert_all_called=False) as mock:
            route = mock.post(f"https://{INDEX_HOST}/query")
            with pytest.raises(Exception):
                await self._service(embeddings).search("   ")
        assert embeddings.query_calls == 0
        assert route.call_count == 0

    async def test_top_k_is_capped_by_configuration(self):
        captured: Dict[str, Any] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"matches": []})

        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(side_effect=capture)
            await self._service().search("anything", top_k=10_000)

        assert captured["topK"] <= 50


# ========================================== TEST 8: meeting <-> vector mapping
@pytest.mark.asyncio
class TestMeetingVectorMapping:
    async def test_every_result_resolves_to_the_right_meeting(self):
        knowledge = FakeKnowledge(MEETINGS)
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "matches": [
                            match("v1", 0.9, meeting_id=MEETING_ID),
                            match("v2", 0.8, meeting_id="meeting-b"),
                        ]
                    },
                )
            )
            response = await SemanticSearchService(
                FakeEmbeddings(), repo(), knowledge
            ).search("launch")

        titles = {r.meeting_id: r.meeting_title for r in response.results}
        assert titles[MEETING_ID] == "Mobile Application Planning"
        assert titles["meeting-b"] == "Database Migration Review"

    async def test_a_match_for_a_deleted_meeting_is_dropped(self):
        """A stale vector must never resurrect a meeting that no longer exists."""
        knowledge = FakeKnowledge(MEETINGS)
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "matches": [
                            match("v1", 0.95, meeting_id="deleted-meeting"),
                            match("v2", 0.80, meeting_id=MEETING_ID),
                        ]
                    },
                )
            )
            response = await SemanticSearchService(
                FakeEmbeddings(), repo(), knowledge
            ).search("launch")

        assert [r.meeting_id for r in response.results] == [MEETING_ID]

    async def test_a_match_keeps_its_source_trace(self):
        with respx.mock as mock:
            mock.post(f"https://{INDEX_HOST}/query").mock(
                return_value=httpx.Response(
                    200, json={"matches": [match("v1", 0.9, source_type="action_item")]}
                )
            )
            response = await SemanticSearchService(
                FakeEmbeddings(), repo(), FakeKnowledge(MEETINGS)
            ).search("deadline")

        single = response.results[0].matches[0]
        assert single.meeting_id == MEETING_ID
        assert single.source_type == "action_item"
        assert single.source_id == "transcript"
        assert single.chunk_index == 0


# ================================================ indexing safety + backfill
@pytest.mark.asyncio
class TestIndexingResilience:
    async def test_a_failed_index_is_recorded_not_raised(self):
        """Task 45: Supabase saved, Pinecone failed -> the meeting survives."""
        knowledge = FakeKnowledge(MEETINGS)
        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(500, json={"message": "unavailable"})
            )
            service = KnowledgeIndexService(knowledge, FakeEmbeddings(), repo())
            result = await service.index_meeting(MEETING_ID)

        assert result.status is IndexStatus.FAILED
        assert result.error_code
        assert (MEETING_ID, IndexStatus.FAILED) in knowledge.statuses

    async def test_backfill_indexes_historical_meetings_sequentially(self):
        knowledge = FakeKnowledge(MEETINGS)
        embeddings = FakeEmbeddings()
        with respx.mock as mock:
            mock.get(f"https://{INDEX_HOST}/vectors/list").mock(
                return_value=httpx.Response(200, json={"vectors": [], "pagination": {}})
            )
            mock.post(f"https://{INDEX_HOST}/vectors/upsert").mock(
                return_value=httpx.Response(200, json={})
            )
            service = KnowledgeIndexService(knowledge, embeddings, repo())
            summary = await service.index_all()

        assert summary.processed == len(MEETINGS)
        assert summary.indexed == len(MEETINGS)
        assert summary.failed == 0

    async def test_indexing_requires_configuration_before_doing_anything(self):
        service = KnowledgeIndexService(
            FakeKnowledge(MEETINGS), FakeEmbeddings(configured=False), repo()
        )
        with pytest.raises(Exception):
            await service.index_meeting(MEETING_ID)

    async def test_a_meeting_with_nothing_to_index_is_reported_cleanly(self):
        class EmptyKnowledge(FakeKnowledge):
            def get_meeting_bundle(self, meeting_id):
                bundle = make_bundle(
                    summary="", key_points=[], decisions=[], action_items=[], participants=[]
                )
                bundle["meeting"] = {**bundle["meeting"], "transcript_text": ""}
                return bundle

        service = KnowledgeIndexService(
            EmptyKnowledge(MEETINGS), FakeEmbeddings(), repo()
        )
        result = await service.index_meeting(MEETING_ID)
        assert result.status is IndexStatus.NOT_INDEXED
        assert result.documents == 0
