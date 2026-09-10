"""Prompt templates for meeting intelligence extraction.

Three templates, one per job:

* SINGLE  - a transcript that fits in one request
* CHUNK   - one slice of a long transcript
* MERGE   - combine per-chunk results into one coherent answer

Every template repeats the same anti-hallucination contract, because that rule
is the difference between a useful tool and a confident fabricator.
"""

from __future__ import annotations

from typing import List

SYSTEM_PROMPT = """You are a meeting analyst. You read meeting transcripts and \
return structured facts about them.

Absolute rules:
1. Use only what the transcript actually says. Never add, guess or infer facts \
that are not present.
2. If a deadline is not stated, return null. Never invent a date.
3. If nobody is clearly responsible for a task, return null for assigned_to. \
Never assign work to a person who did not accept it.
4. If there were no decisions, return an empty list. An empty list is a correct \
answer.
5. Use people's names exactly as they appear in the transcript. Do not invent \
surnames, titles or roles.
6. Return only a single JSON object. No prose, no explanation, no markdown code \
fences.
"""

_SCHEMA_BLOCK = """Return exactly this JSON shape:

{
  "summary": "string - 3 to 6 sentences, neutral, factual",
  "key_points": ["string - the substantive things discussed"],
  "decisions": ["string - only things the group actually decided"],
  "participants": ["string - names of people who spoke or were addressed"],
  "action_items": [
    {
      "task": "string - what must be done, starting with a verb",
      "assigned_to": "string name, or null if the transcript does not say",
      "deadline": "string exactly as spoken (e.g. 'Friday', 'next sprint'), or null",
      "priority": "high | medium | low",
      "status": "pending | in_progress | completed | blocked",
      "context": "string - short quote or paraphrase showing where this came from"
    }
  ]
}

Field rules:
- priority: "high" only when urgency is stated or clearly implied (a blocker, a \
launch dependency, an explicit "urgent"). Otherwise "medium". Use "low" for \
optional or nice-to-have work.
- status: "completed" only if the transcript says the work is already done, \
"in_progress" if it has started, "blocked" if something is stopping it, \
otherwise "pending".
- Do not repeat the same task twice.
- Do not turn general discussion into an action item. An action item needs \
someone to do something."""


def build_single_pass_prompt(transcript: str, meeting_title: str = "") -> str:
    """Prompt for a transcript that fits inside one request."""
    title_line = f"Meeting title: {meeting_title}\n" if meeting_title else ""
    return f"""{title_line}Analyse the meeting transcript below and extract structured intelligence.

{_SCHEMA_BLOCK}

TRANSCRIPT:
\"\"\"
{transcript}
\"\"\"

Return only the JSON object."""


def build_chunk_prompt(
    chunk: str, chunk_index: int, total_chunks: int, meeting_title: str = ""
) -> str:
    """Prompt for one slice of a long transcript (map step)."""
    title_line = f"Meeting title: {meeting_title}\n" if meeting_title else ""
    return f"""{title_line}This is part {chunk_index + 1} of {total_chunks} of one long meeting transcript.
Analyse only this part. Do not speculate about what happened in the other parts.

{_SCHEMA_BLOCK}

TRANSCRIPT PART {chunk_index + 1} OF {total_chunks}:
\"\"\"
{chunk}
\"\"\"

Return only the JSON object."""


def build_merge_prompt(partial_results: List[str], meeting_title: str = "") -> str:
    """Prompt that merges per-chunk JSON into one result (reduce step)."""
    title_line = f"Meeting title: {meeting_title}\n" if meeting_title else ""
    joined = "\n\n".join(
        f"--- ANALYSIS OF PART {index + 1} ---\n{payload}"
        for index, payload in enumerate(partial_results)
    )
    return f"""{title_line}Below are separate analyses of consecutive parts of one meeting.
Merge them into a single analysis of the whole meeting.

Merge rules:
- Write one summary that covers the whole meeting, not a list of part summaries.
- Remove duplicates: the same action item, decision or key point appearing in \
two parts becomes one entry.
- The same person named in several parts is one participant. Keep the fullest \
form of each name.
- Keep every distinct action item. Never drop one because it seems minor.
- Do not add anything that is absent from all the analyses below.
- Keep null values null. Do not fill in a missing deadline or assignee during the merge.

{_SCHEMA_BLOCK}

{joined}

Return only the merged JSON object."""
