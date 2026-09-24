"""Grounded question answering over meeting records (Milestone 3, Task 5)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.controllers.rag_controller import RAGController
from app.schemas.common import ErrorResponse
from app.schemas.rag import AskRequest
from app.utils.responses import success_payload

router = APIRouter(prefix="/meetings", tags=["Meeting Q&A"])


def get_controller() -> RAGController:
    return RAGController()


@router.post(
    "/ask",
    summary="Ask a question about past meetings",
    description=(
        "Retrieval-Augmented Generation over the meeting knowledge index.\n\n"
        "The question is embedded, the most relevant passages are retrieved from "
        "the vector index, the matching meeting records are read from Supabase, and "
        "only that context is sent to **Groq** - the same LLM service used for meeting "
        "analysis - in a single request.\n\n"
        "The answer is grounded: the model is instructed to use only the retrieved "
        "context, cited meetings are checked against what was actually retrieved, and "
        "when the records do not contain the answer the response says so with "
        "`answer_found: false` rather than inventing one.\n\n"
        "`sources` lists the meetings behind the answer so it can be verified."
    ),
    responses={
        422: {"model": ErrorResponse, "description": "Empty or invalid question"},
        503: {
            "model": ErrorResponse,
            "description": "Embeddings, the vector index or the AI providers are not configured",
        },
    },
)
async def ask_meetings(
    payload: AskRequest = Body(
        ...,
        examples=[{"question": "What deadline was decided for the mobile application?"}],
    ),
    controller: RAGController = Depends(get_controller),
):
    result = await controller.ask(
        payload.question, top_k=payload.top_k, filters=payload.filters
    )
    return success_payload(
        result.model_dump(mode="json"),
        result.message or "Answer generated from your meeting records.",
    )
