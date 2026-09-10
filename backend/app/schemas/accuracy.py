"""Transcription accuracy schemas (Word Error Rate testing)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class AccuracyRequest(BaseModel):
    """Compare a generated transcript against a human reference transcript."""

    reference_transcript: str = Field(
        ..., min_length=1, description="Ground-truth transcript typed or pasted by the user."
    )
    hypothesis_transcript: Optional[str] = Field(
        None,
        description="Transcript to score. Omit it and pass meeting_id to score the stored transcript.",
    )
    meeting_id: Optional[str] = Field(
        None, description="Score the stored transcript of this meeting."
    )
    target_accuracy: float = Field(
        90.0, ge=0, le=100, description="Milestone target, defaults to the 90% requirement."
    )
    ignore_case: bool = True
    ignore_punctuation: bool = True
    ignore_filler_words: bool = True

    @field_validator("reference_transcript")
    @classmethod
    def _reference_has_words(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reference transcript must contain text")
        return value


class WordDiff(BaseModel):
    """One alignment error, with position so the UI can show context."""

    operation: Literal["substitution", "deletion", "insertion"]
    reference_word: Optional[str] = None
    hypothesis_word: Optional[str] = None
    reference_position: Optional[int] = None
    hypothesis_position: Optional[int] = None


class AccuracyResult(BaseModel):
    accuracy_percentage: float = Field(..., description="100 - WER, floored at 0.")
    word_error_rate: float = Field(..., description="WER as a percentage.")
    match_error_rate: float = Field(..., description="(S+D+I) / (S+D+C) as a percentage.")
    word_information_preserved: float = Field(..., description="Correct words / reference words, as a percentage.")

    reference_word_count: int
    hypothesis_word_count: int
    correct_words: int
    substitutions: int = Field(..., description="Incorrect words: heard, but wrong.")
    deletions: int = Field(..., description="Missing words: in the reference, not in the transcript.")
    insertions: int = Field(..., description="Extra words: in the transcript, not in the reference.")

    missing_words: List[str] = Field(default_factory=list)
    incorrect_words: List[WordDiff] = Field(default_factory=list)
    extra_words: List[str] = Field(default_factory=list)
    diffs: List[WordDiff] = Field(default_factory=list)

    target_accuracy: float
    passed: bool
    status: Literal["PASSED", "NEEDS IMPROVEMENT"]
    explanation: str
