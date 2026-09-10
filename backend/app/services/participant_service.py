"""Participant identification and mapping.

Turns the loose name strings a language model produces into one clean, de-duped
participant list per meeting, then links every action item to a participant.

De-duplication rules, from safest to loosest:

1. exact match on the normalised key      "RAVI" / "Ravi" / " ravi " -> one person
2. first-name subset of a full name       "Ravi" + "Ravi Kumar"      -> "Ravi Kumar"
3. high string similarity (>= 0.90)       "Priya" / "Priyaa"         -> one person

Rule 3 is deliberately strict. "Ravi" and "Rahul" score 0.50 and stay separate:
merging two real people is a worse failure than listing one person twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import get_logger
from app.models.enums import UNKNOWN_PARTICIPANT
from app.schemas.intelligence import ActionItem, Participant
from app.utils.text import normalize_person_name, title_case_name

logger = get_logger(__name__)

SIMILARITY_THRESHOLD = 0.90
MIN_FUZZY_LENGTH = 4  # never fuzzy-match very short names such as "Al" and "Ali"

# Strings a model uses for a speaker it could not name.
_UNKNOWN_MARKERS = {
    "unknown", "unknown speaker", "unidentified", "unnamed", "speaker",
    "participant", "someone", "somebody", "team", "everyone", "all", "n/a", "na",
    "n a", "not applicable", "not specified", "not mentioned", "tbd", "null", "none",
}


@dataclass
class _Cluster:
    display_name: str
    normalized: str
    aliases: set = field(default_factory=set)
    mentions: int = 0
    role: Optional[str] = None
    is_unknown: bool = False


class ParticipantService:
    """Builds the participant list and rewrites assignees to canonical names."""

    def build(
        self,
        names: Sequence[str],
        action_items: Sequence[ActionItem],
        *,
        speaker_labels: Sequence[str] = (),
    ) -> Tuple[List[Participant], List[ActionItem]]:
        clusters: List[_Cluster] = []

        # Named participants first so they win as canonical display names.
        for raw in list(names) + list(speaker_labels):
            self._absorb(clusters, raw)

        # Then assignees, which may introduce people not in the participant list.
        for item in action_items:
            if item.assigned_to:
                self._absorb(clusters, item.assigned_to)

        participants = [
            Participant(
                name=cluster.display_name,
                normalized_name=cluster.normalized,
                role=cluster.role,
                is_unknown=cluster.is_unknown,
                mention_count=cluster.mentions,
                aliases=sorted(alias for alias in cluster.aliases if alias != cluster.display_name),
            )
            for cluster in clusters
        ]
        participants.sort(key=lambda p: (p.is_unknown, p.name.lower()))

        mapped_items = self._link_action_items(action_items, clusters)

        logger.info(
            "Mapped %s raw name(s) to %s participant(s)",
            len(names) + len(speaker_labels) + sum(1 for i in action_items if i.assigned_to),
            len(participants),
        )
        return participants, mapped_items

    # ------------------------------------------------------------- internals
    def _absorb(self, clusters: List[_Cluster], raw: Optional[str]) -> Optional[_Cluster]:
        """Add a name to an existing cluster or start a new one."""
        display = title_case_name(str(raw or ""))
        normalized = normalize_person_name(str(raw or ""))

        if not normalized or normalized in _UNKNOWN_MARKERS:
            return None  # anonymous mentions never become participant records

        match = self._find(clusters, normalized)
        if match:
            match.mentions += 1
            match.aliases.add(display)
            # Prefer the fuller name as the display form: "Ravi" -> "Ravi Kumar".
            if len(normalized.split()) > len(match.normalized.split()):
                match.aliases.add(match.display_name)
                match.display_name = display
                match.normalized = normalized
            return match

        cluster = _Cluster(
            display_name=display or UNKNOWN_PARTICIPANT,
            normalized=normalized,
            aliases={display},
            mentions=1,
        )
        clusters.append(cluster)
        return cluster

    @staticmethod
    def _find(clusters: List[_Cluster], normalized: str) -> Optional[_Cluster]:
        # 1. exact
        for cluster in clusters:
            if cluster.normalized == normalized:
                return cluster

        parts = normalized.split()

        # 2. first-name / full-name containment
        for cluster in clusters:
            cluster_parts = cluster.normalized.split()
            if len(parts) == 1 and len(cluster_parts) > 1 and parts[0] == cluster_parts[0]:
                return cluster
            if len(cluster_parts) == 1 and len(parts) > 1 and cluster_parts[0] == parts[0]:
                return cluster

        # 3. near-identical spelling (typos, doubled letters)
        if len(normalized) >= MIN_FUZZY_LENGTH:
            for cluster in clusters:
                if len(cluster.normalized) < MIN_FUZZY_LENGTH:
                    continue
                ratio = SequenceMatcher(None, cluster.normalized, normalized).ratio()
                if ratio >= SIMILARITY_THRESHOLD:
                    return cluster
        return None

    def _link_action_items(
        self, action_items: Sequence[ActionItem], clusters: List[_Cluster]
    ) -> List[ActionItem]:
        """Rewrite each assignee to the canonical display name, or leave it null."""
        index: Dict[str, _Cluster] = {}
        for cluster in clusters:
            index[cluster.normalized] = cluster
            for alias in cluster.aliases:
                alias_key = normalize_person_name(alias)
                if alias_key:
                    index.setdefault(alias_key, cluster)

        mapped: List[ActionItem] = []
        for item in action_items:
            data = item.model_dump()
            normalized = normalize_person_name(item.assigned_to or "")

            if not normalized or normalized in _UNKNOWN_MARKERS:
                # The transcript never named an owner. Say so instead of guessing.
                data["assigned_to"] = None
            else:
                cluster = index.get(normalized) or self._find(clusters, normalized)
                data["assigned_to"] = cluster.display_name if cluster else title_case_name(
                    item.assigned_to or ""
                )
            mapped.append(ActionItem(**data))
        return mapped

    @staticmethod
    def is_unknown_name(raw: Optional[str]) -> bool:
        normalized = normalize_person_name(str(raw or ""))
        return not normalized or normalized in _UNKNOWN_MARKERS
