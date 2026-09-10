"""Transcription accuracy measurement (Word Error Rate).

Implemented directly rather than pulled from a library because the UI needs the
*alignment*, not just the score: which words were missed, which were heard
wrongly, and which were invented.

    WER = (S + D + I) / N          N = number of words in the reference
    Accuracy = 100 - WER%          floored at 0

    S substitutions - a reference word was replaced by a different word
    D deletions     - a reference word is missing from the transcript
    I insertions    - the transcript contains a word that was never said

Insertions are counted against N, so a transcript that adds many extra words can
push WER above 100%, which is why accuracy is clamped at 0 rather than allowed
to go negative. WER is a distance measure, not a percentage of correctness -
`word_information_preserved` (correct / N) is reported alongside it for the more
intuitive "how much did it get right" reading.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from app.config import get_logger
from app.schemas.accuracy import AccuracyResult, WordDiff
from app.utils.text import normalize_for_comparison

logger = get_logger(__name__)

# Alignment is O(N*M); refuse rather than hang the server on huge inputs.
MAX_WORDS = 25_000

_KEEP = "keep"
_SUB = "substitution"
_DEL = "deletion"
_INS = "insertion"


class AccuracyService:
    def compare(
        self,
        reference: str,
        hypothesis: str,
        *,
        target_accuracy: float = 90.0,
        ignore_case: bool = True,
        ignore_punctuation: bool = True,
        ignore_filler_words: bool = True,
    ) -> AccuracyResult:
        reference_words = self._tokenize(
            reference, ignore_case, ignore_punctuation, ignore_filler_words
        )
        hypothesis_words = self._tokenize(
            hypothesis, ignore_case, ignore_punctuation, ignore_filler_words
        )

        if not reference_words:
            raise ValueError("The reference transcript contains no words to compare against.")
        if len(reference_words) > MAX_WORDS or len(hypothesis_words) > MAX_WORDS:
            raise ValueError(
                f"Transcripts longer than {MAX_WORDS:,} words cannot be compared in one request."
            )

        operations = self._align(reference_words, hypothesis_words)

        correct = substitutions = deletions = insertions = 0
        diffs: List[WordDiff] = []
        missing_words: List[str] = []
        extra_words: List[str] = []
        incorrect_words: List[WordDiff] = []

        for operation, ref_index, hyp_index in operations:
            if operation == _KEEP:
                correct += 1
            elif operation == _SUB:
                substitutions += 1
                diff = WordDiff(
                    operation=_SUB,
                    reference_word=reference_words[ref_index],
                    hypothesis_word=hypothesis_words[hyp_index],
                    reference_position=ref_index,
                    hypothesis_position=hyp_index,
                )
                diffs.append(diff)
                incorrect_words.append(diff)
            elif operation == _DEL:
                deletions += 1
                word = reference_words[ref_index]
                missing_words.append(word)
                diffs.append(
                    WordDiff(operation=_DEL, reference_word=word, reference_position=ref_index)
                )
            else:  # insertion
                insertions += 1
                word = hypothesis_words[hyp_index]
                extra_words.append(word)
                diffs.append(
                    WordDiff(operation=_INS, hypothesis_word=word, hypothesis_position=hyp_index)
                )

        reference_count = len(reference_words)
        errors = substitutions + deletions + insertions
        wer = (errors / reference_count) * 100.0
        accuracy = max(0.0, 100.0 - wer)
        denominator = substitutions + deletions + correct
        mer = (errors / denominator * 100.0) if denominator else 0.0
        wip = (correct / reference_count) * 100.0
        passed = accuracy >= target_accuracy

        logger.info(
            "Accuracy comparison: %.2f%% (WER %.2f%%, S=%s D=%s I=%s, N=%s)",
            accuracy, wer, substitutions, deletions, insertions, reference_count,
        )

        return AccuracyResult(
            accuracy_percentage=round(accuracy, 2),
            word_error_rate=round(wer, 2),
            match_error_rate=round(mer, 2),
            word_information_preserved=round(wip, 2),
            reference_word_count=reference_count,
            hypothesis_word_count=len(hypothesis_words),
            correct_words=correct,
            substitutions=substitutions,
            deletions=deletions,
            insertions=insertions,
            missing_words=missing_words[:500],
            incorrect_words=incorrect_words[:500],
            extra_words=extra_words[:500],
            diffs=diffs[:2000],
            target_accuracy=target_accuracy,
            passed=passed,
            status="PASSED" if passed else "NEEDS IMPROVEMENT",
            explanation=self._explain(accuracy, wer, target_accuracy, passed),
        )

    # ------------------------------------------------------------- internals
    @staticmethod
    def _tokenize(
        text: str, ignore_case: bool, ignore_punctuation: bool, ignore_filler_words: bool
    ) -> List[str]:
        if ignore_punctuation:
            tokens = normalize_for_comparison(text, drop_fillers=ignore_filler_words)
            return tokens if ignore_case else text.split()
        tokens = (text or "").split()
        if ignore_case:
            tokens = [token.lower() for token in tokens]
        return tokens

    @staticmethod
    def _align(reference: Sequence[str], hypothesis: Sequence[str]) -> List[Tuple[str, int, int]]:
        """Levenshtein alignment over words, returning the edit path.

        Uses a full DP table (rows x cols) with backtracking. Memory stays
        bounded by the MAX_WORDS guard above.
        """
        rows, cols = len(reference), len(hypothesis)
        distance = [[0] * (cols + 1) for _ in range(rows + 1)]

        for i in range(rows + 1):
            distance[i][0] = i
        for j in range(cols + 1):
            distance[0][j] = j

        for i in range(1, rows + 1):
            ref_word = reference[i - 1]
            row, previous_row = distance[i], distance[i - 1]
            for j in range(1, cols + 1):
                if ref_word == hypothesis[j - 1]:
                    row[j] = previous_row[j - 1]
                else:
                    row[j] = 1 + min(
                        previous_row[j - 1],  # substitution
                        previous_row[j],      # deletion
                        row[j - 1],           # insertion
                    )

        operations: List[Tuple[str, int, int]] = []
        i, j = rows, cols
        while i > 0 or j > 0:
            if i > 0 and j > 0 and reference[i - 1] == hypothesis[j - 1] \
                    and distance[i][j] == distance[i - 1][j - 1]:
                operations.append((_KEEP, i - 1, j - 1))
                i, j = i - 1, j - 1
            elif i > 0 and j > 0 and distance[i][j] == distance[i - 1][j - 1] + 1:
                operations.append((_SUB, i - 1, j - 1))
                i, j = i - 1, j - 1
            elif i > 0 and distance[i][j] == distance[i - 1][j] + 1:
                operations.append((_DEL, i - 1, -1))
                i -= 1
            else:
                operations.append((_INS, -1, j - 1))
                j -= 1

        operations.reverse()
        return operations

    @staticmethod
    def _explain(accuracy: float, wer: float, target: float, passed: bool) -> str:
        base = (
            f"Word Error Rate is {wer:.2f}%, so accuracy is {accuracy:.2f}% "
            f"(accuracy = 100 - WER). Every substituted, missing and extra word counts "
            f"as one error against the number of words in the reference transcript."
        )
        if passed:
            return f"{base} This meets the {target:.0f}% target."
        gap = target - accuracy
        return (
            f"{base} This is {gap:.2f} points below the {target:.0f}% target. "
            "A larger Whisper model (WHISPER_MODEL=small or medium), a cleaner recording, "
            "or setting WHISPER_LANGUAGE explicitly usually closes the gap."
        )
