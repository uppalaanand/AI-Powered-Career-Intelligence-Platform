"""Milestone 3, Tasks 1 & 2 - knowledge repository and embedding generation.

Covers the two halves that turn Supabase rows into embeddable vectors:

* ``build_documents`` - every source type becomes a document, every document
  stays linked to its meeting, and nothing meaningless gets embedded;
* ``EmbeddingService`` / ``FastEmbedProvider`` - the local embedding model that
  replaced Gemini. Unit tests swap the ONNX model for a deterministic
  bag-of-words stand-in, so they need no download and no key. One opt-in test
  runs the REAL model (set ``RUN_REAL_EMBEDDING_TESTS=1``).

Nothing here touches Supabase, Pinecone, Groq or any external API.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import pytest

from app.config import get_settings
from app.knowledge.documents import (
    SOURCE_TYPES,
    KnowledgeDocument,
    build_documents,
    fingerprint,
)
from app.knowledge.embeddings.fastembed_provider import FastEmbedProvider
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embedding_service import EmbeddingService
from app.utils.errors import (
    EmbeddingNotConfiguredError,
    EmbeddingRequestError,
    InvalidQueryError,
)

MEETING_ID = "11111111-2222-3333-4444-555555555555"
DIMENSIONS = get_settings().embedding_dimensions


def make_bundle(**overrides: Any) -> Dict[str, Any]:
    """A realistic meeting bundle, shaped exactly like the repository returns."""
    bundle: Dict[str, Any] = {
        "meeting": {
            "id": MEETING_ID,
            "title": "Mobile Application Planning",
            "created_at": "2026-09-15T10:00:00+00:00",
            "status": "COMPLETED",
            "transcript_text": (
                "Ravi opened the meeting about the mobile application launch. "
                "The team agreed the database migration has to finish first. "
                "Priya said the UI testing report would be ready on Thursday."
            ),
        },
        "summary": "The team planned the mobile application launch and its dependencies.",
        "key_points": [{"id": "kp-1", "text": "The launch depends on the database migration"}],
        "decisions": [
            {"id": "dec-1", "text": "Complete the mobile application launch by Friday",
             "context": "Everyone agreed."}
        ],
        "action_items": [
            {"id": "ai-1", "task": "Finish the database migration", "assigned_to": "Ravi",
             "deadline": "Friday", "priority": "high", "status": "pending",
             "context": "Ravi took this on."}
        ],
        "participants": [
            {"id": "p-1", "name": "Ravi", "role": "Backend"},
            {"id": "p-2", "name": "Priya", "role": None},
        ],
        "has_intelligence": True,
    }
    bundle.update(overrides)
    return bundle


# =========================================================== TEST 1: Task 1
class TestKnowledgeDocuments:
    """Every kind of meeting record becomes a searchable, traceable document."""

    def test_every_source_type_is_represented(self):
        documents = build_documents(make_bundle())
        produced = {document.source_type for document in documents}
        assert produced == set(SOURCE_TYPES), f"missing: {set(SOURCE_TYPES) - produced}"

    def test_transcript_summary_decisions_and_action_items_are_all_retrieved(self):
        documents = build_documents(make_bundle())
        by_type = {document.source_type: document for document in documents}

        assert "database migration" in by_type["transcript"].content
        assert "mobile application launch" in by_type["summary"].content
        assert "Complete the mobile application launch by Friday" in by_type["decision"].content
        assert "Finish the database migration" in by_type["action_item"].content
        assert "database migration" in by_type["key_point"].content
        assert "Ravi" in by_type["participants"].content

    def test_every_document_is_linked_to_its_meeting(self):
        documents = build_documents(make_bundle())
        assert documents
        for document in documents:
            assert document.meeting_id == MEETING_ID
            assert document.to_metadata()["meeting_id"] == MEETING_ID
            assert document.vector_id.startswith(f"{MEETING_ID}#")

    def test_no_orphan_documents_without_a_meeting_id(self):
        bundle = make_bundle()
        bundle["meeting"] = {**bundle["meeting"], "id": ""}
        assert build_documents(bundle) == []

    def test_deadlines_are_searchable_inside_action_items(self):
        """Deadlines get no vector of their own - they ride on the action item."""
        documents = build_documents(make_bundle())
        action = next(d for d in documents if d.source_type == "action_item")
        assert "Deadline: Friday." in action.content
        metadata = action.to_metadata()
        assert metadata["deadline"] == "Friday"
        assert metadata["has_deadline"] is True
        assert metadata["assigned_to"] == "Ravi"
        assert metadata["priority"] == "high"
        assert metadata["status"] == "pending"

    def test_missing_deadline_is_stated_rather_than_invented(self):
        bundle = make_bundle()
        bundle["action_items"] = [{"id": "ai-9", "task": "Review the design", "deadline": None}]
        action = next(d for d in build_documents(bundle) if d.source_type == "action_item")
        assert "Deadline: not stated." in action.content
        assert action.to_metadata()["has_deadline"] is False
        assert "deadline" not in action.to_metadata()

    def test_meeting_title_and_date_travel_with_every_vector(self):
        documents = build_documents(make_bundle())
        for document in documents:
            metadata = document.to_metadata()
            assert metadata["meeting_title"] == "Mobile Application Planning"
            assert metadata["meeting_date"].startswith("2026-09-15")
            assert isinstance(metadata["meeting_date_ts"], float)

    def test_a_transcript_only_meeting_still_indexes(self):
        """A meeting that was transcribed but never analysed is still searchable."""
        bundle = make_bundle(
            summary="", key_points=[], decisions=[], action_items=[], participants=[]
        )
        documents = build_documents(bundle)
        assert documents
        assert {d.source_type for d in documents} == {"transcript"}

    def test_a_meeting_with_nothing_produces_nothing(self):
        bundle = make_bundle(
            summary="", key_points=[], decisions=[], action_items=[], participants=[]
        )
        bundle["meeting"] = {**bundle["meeting"], "transcript_text": ""}
        assert build_documents(bundle) == []

    def test_empty_and_whitespace_records_are_never_embedded(self):
        bundle = make_bundle()
        bundle["decisions"] = [{"id": "d1", "text": "   "}, {"id": "d2", "text": "A real decision"}]
        bundle["key_points"] = [{"id": "k1", "text": ""}]
        decisions = [d for d in build_documents(bundle) if d.source_type == "decision"]
        assert len(decisions) == 1
        assert "A real decision" in decisions[0].content
        assert not any(d.source_type == "key_point" for d in build_documents(bundle))

    def test_long_transcripts_are_chunked_with_ordered_indexes(self):
        bundle = make_bundle()
        bundle["meeting"] = {
            **bundle["meeting"],
            "transcript_text": "The team discussed the launch in detail. " * 400,
        }
        chunks = [d for d in build_documents(bundle) if d.source_type == "transcript"]
        assert len(chunks) > 1
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
        assert len({c.vector_id for c in chunks}) == len(chunks)

    def test_chunk_size_respects_the_knowledge_window(self):
        bundle = make_bundle()
        bundle["meeting"] = {
            **bundle["meeting"],
            "transcript_text": "The team discussed the launch in detail. " * 400,
        }
        ceiling = get_settings().knowledge_chunk_char_size
        for chunk in (d for d in build_documents(bundle) if d.source_type == "transcript"):
            assert len(chunk.content) <= ceiling * 1.2

    def test_stored_content_is_capped_but_the_record_is_untouched(self):
        long_text = "word " * 2000
        document = KnowledgeDocument(
            meeting_id=MEETING_ID, source_type="summary", source_id="s",
            chunk_index=0, content=long_text,
        )
        assert len(document.to_metadata()["content"]) < len(long_text)
        assert document.content == long_text  # the document itself is not mutated

    def test_metadata_drops_values_pinecone_cannot_store(self):
        document = KnowledgeDocument(
            meeting_id=MEETING_ID, source_type="summary", source_id="s", chunk_index=0,
            content="A summary.",
            metadata={"ok_str": "yes", "ok_int": 3, "ok_bool": True,
                      "ok_list": ["a", "b"], "bad_dict": {"x": 1}, "none": None},
        )
        metadata = document.to_metadata()
        assert metadata["ok_str"] == "yes" and metadata["ok_int"] == 3
        assert metadata["ok_bool"] is True and metadata["ok_list"] == ["a", "b"]
        assert "bad_dict" not in metadata and "none" not in metadata


# ================================================= TEST 8: meeting <-> vector
class TestDeterministicIds:
    """Idempotent indexing: the same content must produce the same vector ids."""

    def test_ids_are_stable_across_runs(self):
        first = [d.vector_id for d in build_documents(make_bundle())]
        second = [d.vector_id for d in build_documents(make_bundle())]
        assert first == second
        assert len(set(first)) == len(first), "vector ids must be unique"

    def test_id_encodes_the_full_trace_back_to_the_source(self):
        action = next(d for d in build_documents(make_bundle()) if d.source_type == "action_item")
        meeting_id, source_type, source_id, chunk_index = action.vector_id.split("#")
        assert meeting_id == MEETING_ID
        assert source_type == "action_item"
        assert source_id == "ai-1"
        assert chunk_index == "0"

    def test_every_id_shares_the_meeting_prefix_used_for_deletion(self):
        for document in build_documents(make_bundle()):
            assert document.vector_id.startswith(f"{MEETING_ID}#")

    def test_fingerprint_is_stable_for_unchanged_content(self):
        assert fingerprint(build_documents(make_bundle())) == fingerprint(
            build_documents(make_bundle())
        )

    def test_fingerprint_changes_when_content_changes(self):
        before = fingerprint(build_documents(make_bundle()))
        after = fingerprint(build_documents(make_bundle(summary="A different summary entirely.")))
        assert before != after

    def test_fingerprint_changes_when_the_embedding_model_changes(self):
        """Switching models must force a re-embed, even for unchanged content."""
        documents = build_documents(make_bundle())
        old_model = fingerprint(documents, "old-model@768")
        new_model = fingerprint(documents, "BAAI/bge-small-en-v1.5@384")
        assert old_model != new_model
        assert new_model.startswith("BAAI/bge-small-en-v1.5@384:")


# =========================================================== TEST 2: Task 2
@pytest.mark.asyncio
class TestEmbeddingGeneration:
    """The real FastEmbedProvider code path, with the ONNX model swapped out."""

    async def test_each_source_type_gets_a_real_vector(self):
        """Transcript, summary, decision and action item all receive embeddings."""
        from tests.helpers import fake_embedding_provider

        documents = build_documents(make_bundle())
        pairs = await EmbeddingService(fake_embedding_provider()).embed_documents(documents)

        embedded_types = {document.source_type for document, _ in pairs}
        for required in ("transcript", "summary", "decision", "action_item"):
            assert required in embedded_types
        for _, vector in pairs:
            assert len(vector) == DIMENSIONS
            assert all(isinstance(value, float) for value in vector)

    async def test_vectors_are_generated_from_the_text_not_hardcoded(self):
        from tests.helpers import fake_embedding_provider

        service = EmbeddingService(fake_embedding_provider())
        first = await service.embed_query("database migration plan")
        second = await service.embed_query("mobile application launch")
        assert first != second, "different text must give a different vector"
        assert first == await service.embed_query("database migration plan")

    async def test_passages_are_embedded_in_one_batched_model_call(self):
        from tests.helpers import BagOfWordsModel, fake_embedding_provider

        model = BagOfWordsModel(DIMENSIONS)
        documents = build_documents(make_bundle())
        await EmbeddingService(fake_embedding_provider(model=model)).embed_documents(documents)
        assert model.embed_calls == 1
        assert model.texts_embedded == len(documents)

    async def test_queries_and_passages_use_the_same_model(self):
        """A query is only comparable with passages from the same vector space."""
        from tests.helpers import BagOfWordsModel, fake_embedding_provider

        model = BagOfWordsModel(DIMENSIONS)
        service = EmbeddingService(fake_embedding_provider(model=model))
        await service.embed_documents(build_documents(make_bundle()))
        await service.embed_query("what was decided?")
        assert model.embed_calls == 2, "both sides went through the one model"

    async def test_the_signature_identifies_the_vector_space(self):
        from tests.helpers import fake_embedding_provider

        service = EmbeddingService(fake_embedding_provider(dimensions=DIMENSIONS))
        assert service.signature == f"test/bag-of-words@{DIMENSIONS}"

    async def test_a_wrong_sized_vector_is_rejected_before_it_is_stored(self):
        from app.utils.errors import VectorDimensionMismatchError
        from tests.helpers import BagOfWordsModel

        provider = FastEmbedProvider(
            model="test/model", dimensions=DIMENSIONS,
            model_factory=lambda *_: BagOfWordsModel(DIMENSIONS + 1),
        )
        with pytest.raises(VectorDimensionMismatchError) as exc:
            await provider.embed_query("anything")
        assert "EMBEDDING_DIMENSIONS" in exc.value.message

    async def test_a_malformed_vector_is_rejected(self):
        class Broken:
            def embed(self, texts, batch_size=32):
                return [["not", "numbers"] for _ in texts]

        provider = FastEmbedProvider(model="t", dimensions=2, model_factory=lambda *_: Broken())
        with pytest.raises(EmbeddingRequestError) as exc:
            await provider.embed_query("anything")
        assert exc.value.code == "EMBEDDING_MALFORMED"

    async def test_a_model_that_returns_too_few_vectors_is_rejected(self):
        class Short:
            def embed(self, texts, batch_size=32):
                return [[0.1, 0.2]]

        provider = FastEmbedProvider(model="t", dimensions=2, model_factory=lambda *_: Short())
        with pytest.raises(EmbeddingRequestError):
            await provider.embed_documents(["one passage", "two passages"])

    async def test_an_empty_query_is_rejected_before_the_model_runs(self):
        from tests.helpers import BagOfWordsModel, fake_embedding_provider

        model = BagOfWordsModel(DIMENSIONS)
        with pytest.raises(InvalidQueryError):
            await EmbeddingService(fake_embedding_provider(model=model)).embed_query("   ")
        assert model.embed_calls == 0

    async def test_meaningless_passages_are_skipped_not_embedded(self):
        from tests.helpers import BagOfWordsModel, fake_embedding_provider

        model = BagOfWordsModel(DIMENSIONS)
        documents = [
            KnowledgeDocument(MEETING_ID, "decision", "d1", 0, "ok"),
            KnowledgeDocument(MEETING_ID, "decision", "d2", 0, "A genuinely real decision here"),
        ]
        pairs = await EmbeddingService(fake_embedding_provider(model=model)).embed_documents(
            documents
        )
        assert len(pairs) == 1
        assert model.texts_embedded == 1

    async def test_the_model_is_loaded_once_and_reused(self):
        from tests.helpers import BagOfWordsModel

        loads: List[int] = []

        def factory(*_):
            loads.append(1)
            return BagOfWordsModel(DIMENSIONS)

        provider = FastEmbedProvider(model="t", dimensions=DIMENSIONS, model_factory=factory)
        for text in ("one", "two", "three"):
            await provider.embed_query(text)
        assert len(loads) == 1

    async def test_an_unknown_provider_disables_search_without_crashing(self, monkeypatch):
        """A stale EMBEDDING_PROVIDER=gemini must not break the rest of the app."""
        from app.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
        try:
            service = EmbeddingService()          # must not raise
            assert service.is_configured is False
            with pytest.raises(EmbeddingNotConfiguredError) as exc:
                await service.embed_query("anything")
            assert "fastembed" in exc.value.message
        finally:
            monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
            get_settings.cache_clear()

    async def test_embeddings_need_no_api_key(self):
        from app.config import get_settings

        settings = get_settings()
        assert settings.embedding_provider == "fastembed"
        assert not hasattr(settings, "embedding_api_key")
        assert settings.embeddings_configured is True


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_EMBEDDING_TESTS") != "1",
    reason="Runs the real ONNX model (downloads ~67 MB once). "
           "Set RUN_REAL_EMBEDDING_TESTS=1 to run.",
)
@pytest.mark.asyncio
class TestRealEmbeddingModel:
    """The actual BAAI/bge-small-en-v1.5 model, end to end."""

    async def test_real_vectors_have_the_configured_dimension(self):
        service = EmbeddingService()
        vector = await service.embed_query("Which meeting discussed the database migration?")
        assert len(vector) == get_settings().embedding_dimensions == 384

    async def test_real_model_ranks_by_meaning_not_keywords(self):
        """'database migration' must match 'move the Postgres schema' - no shared words."""
        import numpy as np

        service = EmbeddingService()
        query = np.array(await service.embed_query("Which meeting discussed the database migration?"))
        passages = [
            "We need to move the Postgres schema over before the release.",
            "The office coffee machine is broken again.",
        ]
        documents = [KnowledgeDocument(MEETING_ID, "transcript", "t", i, text)
                     for i, text in enumerate(passages)]
        vectors = [np.array(v) for _, v in await service.embed_documents(documents)]
        scores = [float(query @ v / (np.linalg.norm(query) * np.linalg.norm(v))) for v in vectors]
        assert scores[0] > scores[1] + 0.1


# ==================================== Task 1: repository composes, not copies
class TestKnowledgeRepository:
    """The bundle reader reuses the existing repositories rather than re-querying."""

    def test_bundle_combines_meeting_and_intelligence_records(self):
        class FakeMeetings:
            def get_or_404(self, meeting_id):
                return {"id": meeting_id, "title": "Planning", "transcript_text": "Hello."}

        class FakeIntelligence:
            def get_intelligence(self, meeting_id):
                return {
                    "summary": "A summary.",
                    "key_points": [{"text": "A point"}],
                    "decisions": [{"text": "A decision"}],
                    "action_items": [{"task": "A task"}],
                    "participants": [{"name": "Ravi"}],
                }

        bundle = KnowledgeRepository(FakeMeetings(), FakeIntelligence()).get_meeting_bundle("m1")

        assert bundle["meeting"]["id"] == "m1"
        assert bundle["summary"] == "A summary."
        assert bundle["key_points"] and bundle["decisions"]
        assert bundle["action_items"] and bundle["participants"]
        assert bundle["has_intelligence"] is True

    def test_bundle_is_complete_even_without_analysis(self):
        class FakeMeetings:
            def get_or_404(self, meeting_id):
                return {"id": meeting_id, "title": "Planning", "transcript_text": "Hello."}

        class FakeIntelligence:
            def get_intelligence(self, meeting_id):
                return None

        bundle = KnowledgeRepository(FakeMeetings(), FakeIntelligence()).get_meeting_bundle("m1")

        assert bundle["has_intelligence"] is False
        for key in ("key_points", "decisions", "action_items", "participants"):
            assert bundle[key] == []
        assert bundle["summary"] == ""
