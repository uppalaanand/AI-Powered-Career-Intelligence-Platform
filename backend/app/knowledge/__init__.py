"""Milestone 3 - meeting knowledge repository, embeddings and retrieval.

    Supabase rows -> KnowledgeDocument[] -> embeddings -> Pinecone -> search/RAG

``documents.py`` is pure (rows in, documents out) so Task 1 and Task 2 can be
tested without any credentials at all.
"""

from app.knowledge.documents import (
    SOURCE_TYPES,
    KnowledgeDocument,
    build_documents,
    fingerprint,
)

__all__ = ["SOURCE_TYPES", "KnowledgeDocument", "build_documents", "fingerprint"]
