"""Transcript chunking tests (Milestone 2: token / context limit handling)."""

from __future__ import annotations

from app.ai.chunking import estimate_tokens, plan_chunks


class TestChunkPlanning:
    def test_short_transcript_is_not_chunked(self):
        plan = plan_chunks("A short meeting transcript.", chunk_size=9000)
        assert plan.count == 1
        assert plan.was_chunked is False

    def test_long_transcript_is_split(self):
        text = "This is a sentence about the project. " * 800  # ~30k chars
        plan = plan_chunks(text, chunk_size=9000, overlap=600)
        assert plan.count > 1
        assert plan.was_chunked is True

    def test_chunks_respect_the_size_limit(self):
        text = "The team discussed the launch plan in detail. " * 900
        plan = plan_chunks(text, chunk_size=5000, overlap=300)
        assert all(chunk.char_count <= 5600 for chunk in plan.chunks)

    def test_chunks_are_indexed_in_order(self):
        text = "Sentence number one here. " * 900
        plan = plan_chunks(text, chunk_size=4000, overlap=200)
        assert [chunk.index for chunk in plan.chunks] == list(range(plan.count))

    def test_overlap_repeats_context_between_chunks(self):
        text = " ".join(f"Sentence {i} about the release." for i in range(600))
        with_overlap = plan_chunks(text, chunk_size=4000, overlap=800)
        without_overlap = plan_chunks(text, chunk_size=4000, overlap=0)
        assert sum(c.char_count for c in with_overlap.chunks) > \
               sum(c.char_count for c in without_overlap.chunks)

    def test_max_chunks_ceiling_is_enforced(self):
        text = "A sentence that keeps going on and on. " * 5000
        plan = plan_chunks(text, chunk_size=1000, overlap=0, max_chunks=5)
        assert plan.count == 5
        assert plan.was_truncated is True

    def test_empty_transcript_produces_no_chunks(self):
        plan = plan_chunks("")
        assert plan.count == 0
        assert plan.total_chars == 0

    def test_single_enormous_sentence_is_hard_split(self):
        text = "word " * 6000  # no sentence punctuation at all
        plan = plan_chunks(text, chunk_size=2000, overlap=100)
        assert plan.count > 1
        assert all(chunk.text for chunk in plan.chunks)

    def test_no_content_is_silently_dropped_without_overlap(self):
        text = " ".join(f"Sentence {i}." for i in range(400))
        plan = plan_chunks(text, chunk_size=2000, overlap=0)
        rejoined = " ".join(chunk.text for chunk in plan.chunks)
        assert "Sentence 0." in rejoined
        assert "Sentence 399." in rejoined


class TestTokenEstimation:
    def test_estimate_scales_with_length(self):
        assert estimate_tokens("a" * 4000) > estimate_tokens("a" * 400)

    def test_empty_text_estimates_at_least_one_token(self):
        assert estimate_tokens("") == 1
