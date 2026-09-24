"""LLM layer. Groq is the only provider.

    LLMService  - every LLM task (meeting analysis, RAG answers)
    GroqClient  - the only code that talks to the Groq API
"""

from app.ai.groq_client import GroqClient
from app.ai.llm_service import LLMService

__all__ = ["GroqClient", "LLMService"]
