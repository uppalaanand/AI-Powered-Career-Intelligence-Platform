"""Prompt templates for grounded question answering over meeting records.

Sits beside ``meeting_intelligence_prompt.py`` and follows the same contract:
one system prompt that states the absolute rules, one builder that assembles
the user message, and a JSON-only response.

The difference is where the facts come from. Meeting analysis reads one
transcript; this reads *retrieved passages from several past meetings*, so the
prompt has one extra job: keeping those meetings apart. Context blocks are
labelled with their meeting, date and source type, and the model is told to
attribute every claim - which is what stops "Meeting A decided Friday" and
"Meeting B decided next sprint" from blurring into one confident wrong answer.
"""

from __future__ import annotations

from typing import List

SYSTEM_PROMPT = """You answer questions about a company's past meetings, using \
only the meeting records provided to you.

Absolute rules:
1. Use ONLY the MEETING CONTEXT provided in the user message. It is your only \
source of truth.
2. Never invent information. Do not add participants, dates, deadlines, \
decisions or action items that are not in the context.
3. If the context does not contain the answer, say so plainly and set \
"answer_found" to false. An honest "I could not find that" is the correct \
answer, and is always better than a guess.
4. Do not use general knowledge about the world, about software projects, or \
about what a meeting "usually" decides. If it is not in the context, you do \
not know it.
5. Quote dates, names, deadlines and decisions EXACTLY as the context states \
them. Do not convert "Friday" into a calendar date, and do not tidy up a name.
6. When several meetings are relevant, keep them distinct. Say which meeting \
each fact came from rather than merging them into one answer.
7. Reference meetings by their title, and list every meeting you used in \
"used_meeting_ids".
8. Separate fact from uncertainty. State what the records say plainly; if \
they only hint at something, say that it is not stated explicitly and lower \
"confidence" accordingly.
9. Be concise: answer the question asked, in as few sentences as it needs.
10. Return only a single JSON object. No prose, no explanation, no markdown \
code fences.
"""

_SCHEMA_BLOCK = """Return exactly this JSON shape:

{
  "answer": "string - a direct answer in 1 to 5 sentences, using only the context",
  "answer_found": true or false,
  "used_meeting_ids": ["string - the meeting_id of every meeting you used"],
  "confidence": "high | medium | low"
}

Field rules:
- answer_found: false when the context does not contain the answer. In that \
case "answer" must say that the meeting records do not contain the \
information, and "used_meeting_ids" must be empty.
- used_meeting_ids: copy the MEETING_ID values from the context blocks you \
actually used. Never invent an id.
- confidence: "high" when the context states the answer directly, "medium" \
when it is implied but not stated outright, "low" when the context is only \
loosely related."""


def build_answer_prompt(question: str, context_blocks: List[str]) -> str:
    """Assemble the user message: the question plus the retrieved context.

    ``context_blocks`` are pre-formatted by the RAG service (see
    :func:`format_context_block`), one per retrieved passage, already grouped
    by meeting so the model reads a meeting's evidence together.
    """
    context = "\n\n".join(context_blocks) if context_blocks else "(no meeting records were found)"
    return f"""Answer the question below using only the meeting records in the MEETING CONTEXT.

QUESTION:
{question}

{_SCHEMA_BLOCK}

MEETING CONTEXT:
\"\"\"
{context}
\"\"\"

Remember: if the MEETING CONTEXT above does not answer the question, set \
"answer_found" to false and say the information is not in the meeting records. \
Do not guess.

Return only the JSON object."""


def format_context_block(
    *,
    meeting_id: str,
    meeting_title: str,
    meeting_date: str,
    source_type: str,
    content: str,
) -> str:
    """One labelled evidence block.

    Every field is spelled out so the model can attribute a fact to the right
    meeting instead of guessing which of several it belongs to.
    """
    return (
        f"MEETING: {meeting_title}\n"
        f"MEETING_ID: {meeting_id}\n"
        f"DATE: {meeting_date or 'unknown'}\n"
        f"SOURCE TYPE: {source_type}\n"
        f"CONTENT: {content}"
    )


#: Returned verbatim when retrieval finds nothing, so the API answers without
#: spending an LLM request to be told what is already known.
NO_CONTEXT_ANSWER = (
    "I couldn't find enough information in the meeting records to answer that "
    "question."
)
