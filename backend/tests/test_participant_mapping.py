"""Participant mapping tests (Milestone 2: normalisation and de-duplication)."""

from __future__ import annotations

import pytest

from app.schemas.intelligence import ActionItem
from app.services.participant_service import ParticipantService
from app.utils.text import normalize_person_name, title_case_name


@pytest.fixture
def service() -> ParticipantService:
    return ParticipantService()


class TestNameNormalization:
    @pytest.mark.parametrize("raw", ["ravi", "Ravi", "RAVI", "  Ravi  ", "ravi."])
    def test_casing_and_spacing_collapse_to_one_key(self, raw):
        assert normalize_person_name(raw) == "ravi"

    def test_titles_are_stripped(self):
        assert normalize_person_name("Dr. Priya Sharma") == "priya sharma"
        assert normalize_person_name("Mr Ravi") == "ravi"

    def test_display_form_is_title_cased(self):
        assert title_case_name("ravi kumar") == "Ravi Kumar"

    def test_blank_input_returns_empty_key(self):
        assert normalize_person_name("") == ""
        assert normalize_person_name("   ") == ""


class TestDeduplication:
    def test_same_name_in_different_cases_becomes_one_participant(self, service):
        participants, _ = service.build(["ravi", "Ravi", "RAVI"], [])
        assert len(participants) == 1
        assert participants[0].name == "Ravi"
        assert participants[0].mention_count == 3

    def test_first_name_merges_into_full_name(self, service):
        participants, _ = service.build(["Ravi", "Ravi Kumar"], [])
        assert len(participants) == 1
        assert participants[0].name == "Ravi Kumar"
        assert "Ravi" in participants[0].aliases

    def test_near_identical_spelling_merges(self, service):
        participants, _ = service.build(["Priya", "Priyaa"], [])
        assert len(participants) == 1

    def test_genuinely_different_people_are_not_merged(self, service):
        """The important negative case: never merge two real people."""
        participants, _ = service.build(["Ravi", "Rahul", "Priya"], [])
        assert len(participants) == 3

    def test_multiple_participants_supported(self, service):
        participants, _ = service.build(["Ravi", "Priya", "Anita", "Vikram"], [])
        assert len(participants) == 4

    def test_participants_are_sorted_by_name(self, service):
        participants, _ = service.build(["Vikram", "Anita", "Priya"], [])
        assert [p.name for p in participants] == ["Anita", "Priya", "Vikram"]


class TestUnknownParticipants:
    @pytest.mark.parametrize(
        "placeholder", ["Unknown", "unknown speaker", "Speaker", "someone", "N/A", "team", ""]
    )
    def test_placeholder_names_do_not_become_participants(self, service, placeholder):
        participants, _ = service.build([placeholder], [])
        assert participants == []

    def test_unknown_assignee_becomes_null_not_invented(self, service):
        items = [ActionItem(task="Ship the release", assigned_to="Unknown")]
        _, mapped = service.build([], items)
        assert mapped[0].assigned_to is None

    def test_missing_assignee_stays_null(self, service):
        items = [ActionItem(task="Investigate the crash", assigned_to=None)]
        _, mapped = service.build([], items)
        assert mapped[0].assigned_to is None


class TestActionItemLinking:
    def test_assignee_rewritten_to_canonical_name(self, service):
        items = [ActionItem(task="Finish the API integration", assigned_to="ravi")]
        participants, mapped = service.build(["Ravi Kumar"], items)
        assert mapped[0].assigned_to == "Ravi Kumar"
        assert len(participants) == 1

    def test_assignee_not_in_participant_list_is_still_added(self, service):
        items = [ActionItem(task="Prepare the report", assigned_to="Priya")]
        participants, mapped = service.build(["Ravi"], items)
        assert {p.name for p in participants} == {"Ravi", "Priya"}
        assert mapped[0].assigned_to == "Priya"

    def test_responsibilities_stay_with_the_right_person(self, service):
        items = [
            ActionItem(task="API integration", assigned_to="ravi"),
            ActionItem(task="UI testing report", assigned_to="PRIYA"),
        ]
        _, mapped = service.build(["Ravi", "Priya"], items)
        assert mapped[0].assigned_to == "Ravi"
        assert mapped[1].assigned_to == "Priya"

    def test_duplicate_participant_records_are_never_created(self, service):
        items = [
            ActionItem(task="Task one", assigned_to="Ravi"),
            ActionItem(task="Task two", assigned_to="ravi"),
            ActionItem(task="Task three", assigned_to="RAVI"),
        ]
        participants, _ = service.build(["Ravi"], items)
        assert len(participants) == 1
