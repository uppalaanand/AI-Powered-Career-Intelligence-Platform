"""Word Error Rate / accuracy tests (Milestone 1: transcription accuracy testing)."""

from __future__ import annotations

import pytest

from app.services.accuracy_service import AccuracyService


@pytest.fixture
def service() -> AccuracyService:
    return AccuracyService()


class TestPerfectAndTotalFailure:
    def test_identical_transcripts_score_100(self, service):
        text = "The team discussed the mobile application launch."
        result = service.compare(text, text)
        assert result.accuracy_percentage == 100.0
        assert result.word_error_rate == 0.0
        assert result.substitutions == result.deletions == result.insertions == 0
        assert result.status == "PASSED"

    def test_case_and_punctuation_differences_are_ignored_by_default(self, service):
        result = service.compare("Ravi will finish the API integration.", "ravi will finish the api integration")
        assert result.accuracy_percentage == 100.0

    def test_completely_different_text_scores_low(self, service):
        result = service.compare("alpha bravo charlie delta", "one two three four")
        assert result.accuracy_percentage == 0.0
        assert result.substitutions == 4

    def test_accuracy_never_goes_negative(self, service):
        """Many insertions can push WER above 100; accuracy floors at 0."""
        result = service.compare("hello", "hello " + " ".join(["extra"] * 20))
        assert result.word_error_rate > 100
        assert result.accuracy_percentage == 0.0


class TestErrorClassification:
    def test_substitution_is_counted_as_incorrect_word(self, service):
        result = service.compare("ravi will finish the report", "robbie will finish the report")
        assert result.substitutions == 1
        assert result.deletions == 0 and result.insertions == 0
        assert result.incorrect_words[0].reference_word == "ravi"
        assert result.incorrect_words[0].hypothesis_word == "robbie"

    def test_deletion_is_reported_as_missing_word(self, service):
        result = service.compare("ravi will finish the api integration", "ravi will finish integration")
        assert result.deletions == 2
        assert set(result.missing_words) == {"the", "api"}

    def test_insertion_is_reported_as_extra_word(self, service):
        result = service.compare("meeting starts now", "meeting starts right now")
        assert result.insertions == 1
        assert result.extra_words == ["right"]

    def test_counts_are_internally_consistent(self, service):
        reference = "the team discussed the mobile application launch on friday morning"
        hypothesis = "the team discussed the mobile app launch friday morning today"
        result = service.compare(reference, hypothesis)
        assert result.correct_words + result.substitutions + result.deletions == result.reference_word_count
        assert result.correct_words + result.substitutions + result.insertions == result.hypothesis_word_count

    def test_wer_formula_matches_reported_numbers(self, service):
        result = service.compare(
            "the team discussed the mobile application launch",
            "the team discussed the mobile app launch tomorrow",
        )
        errors = result.substitutions + result.deletions + result.insertions
        expected = errors / result.reference_word_count * 100
        assert result.word_error_rate == pytest.approx(expected, abs=0.01)
        assert result.accuracy_percentage == pytest.approx(100 - expected, abs=0.01)


class TestTargetHandling:
    def test_status_passes_when_above_target(self, service):
        reference = " ".join(f"word{i}" for i in range(100))
        hypothesis = " ".join(f"word{i}" for i in range(95)) + " wrong wrong wrong wrong wrong"
        result = service.compare(reference, hypothesis, target_accuracy=90)
        assert result.accuracy_percentage == 95.0
        assert result.passed is True
        assert result.status == "PASSED"

    def test_status_fails_when_below_target(self, service):
        reference = " ".join(f"word{i}" for i in range(100))
        hypothesis = " ".join(f"word{i}" for i in range(80)) + " " + " ".join(["wrong"] * 20)
        result = service.compare(reference, hypothesis, target_accuracy=90)
        assert result.passed is False
        assert result.status == "NEEDS IMPROVEMENT"
        assert "below" in result.explanation

    def test_custom_target_is_respected(self, service):
        result = service.compare("one two three four", "one two three five", target_accuracy=50)
        assert result.accuracy_percentage == 75.0
        assert result.passed is True

    def test_explanation_states_the_wer_relationship(self, service):
        result = service.compare("one two three", "one two three")
        assert "100 - WER" in result.explanation


class TestFillerWordsAndEdgeCases:
    def test_filler_words_ignored_by_default(self, service):
        result = service.compare("we will ship on friday", "um we will uh ship on friday")
        assert result.accuracy_percentage == 100.0

    def test_filler_words_counted_when_requested(self, service):
        result = service.compare("we will ship", "um we will ship", ignore_filler_words=False)
        assert result.insertions == 1

    def test_empty_reference_raises(self, service):
        with pytest.raises(ValueError):
            service.compare("   ", "anything at all")

    def test_empty_hypothesis_means_everything_missing(self, service):
        result = service.compare("one two three", "")
        assert result.deletions == 3
        assert result.accuracy_percentage == 0.0
