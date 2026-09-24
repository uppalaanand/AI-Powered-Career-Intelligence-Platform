"""Application configuration.

Every value is read from environment variables (see ``.env.example``).
Nothing in this file may contain a real secret.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed, validated view of the environment."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----------------------------------------------------------------- app
    app_name: str = "AI Career Intelligence Platform"
    app_version: str = "1.0.0"
    app_env: Literal["development", "production", "test"] = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # ---------------------------------------------------------------- cors
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -------------------------------------------------------------- upload
    max_upload_size_mb: int = 200
    min_upload_size_bytes: int = 1024
    temp_dir: Optional[str] = None
    keep_temp_files: bool = False

    # -------------------------------------------------------------- ffmpeg
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_timeout_seconds: int = 900
    audio_sample_rate: int = 16000
    audio_channels: int = 1

    # ------------------------------------------------------------- whisper
    whisper_backend: Literal["faster-whisper", "openai"] = "faster-whisper"
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: Optional[str] = None
    whisper_beam_size: int = 5
    whisper_vad_filter: bool = True

    # -------------------------------------------------------- groq (the LLM)
    # Groq is the application's only LLM provider: meeting analysis and RAG
    # answers both go through app/ai/groq_client.py. There is no fallback.
    groq_api_key: Optional[str] = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: int = 60
    # Requests one prompt may spend on transient failures (network, 5xx).
    # 1 = never retry. Auth, model and quota errors are never retried.
    groq_max_attempts: int = 2
    groq_temperature: float = 0.1
    # Only sent to reasoning models (openai/gpt-oss-*, qwen/qwen3-*). "low"
    # keeps reasoning tokens - which count against the output budget and the
    # plan's token quota - to a minimum. Blank = never send it.
    groq_reasoning_effort: Optional[Literal["low", "medium", "high"]] = "low"
    # On a 429, wait and retry once only if Groq asks for at most this long. A
    # longer wait means a daily quota is exhausted, so the request fails fast.
    groq_max_rate_limit_wait_seconds: float = 20.0

    # ------------------------------------------------- LLM request shaping
    # Corrective re-prompts when a reply is not valid JSON. 2 = one call plus
    # one correction; 1 = the strict minimum of quota.
    llm_schema_retry_attempts: int = 2
    # Chunks of a long transcript analysed at once. 1 suits a free Groq plan's
    # tokens-per-minute limit; raise it on a paid plan for faster long meetings.
    llm_chunk_concurrency: int = 1
    llm_max_output_tokens: int = 3000         # single-pass analysis and merge
    llm_chunk_max_output_tokens: int = 2000   # each chunk of a long transcript
    # Prompt + output budget for one request. A chunk-merge prompt that would
    # exceed it is merged locally instead of being sent to fail with a 413.
    llm_max_request_tokens: int = 7000

    # ------------------------------------------------------------ chunking
    llm_chunk_char_size: int = 9000
    llm_chunk_overlap_chars: int = 600
    llm_max_chunks: int = 40

    # ------------------------------------------- milestone 3: embeddings
    # Embeddings are computed LOCALLY with fastembed (ONNX Runtime, no
    # PyTorch): free, no API key, no quota, no network call per search. Groq
    # is an LLM provider and does not produce embeddings. The same model embeds
    # stored passages and search queries, so they share one vector space.
    embedding_provider: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # MUST equal the model's output size AND the Pinecone index dimension.
    # Checked when the model loads and before every upsert.
    embedding_dimensions: int = 384
    embedding_batch_size: int = 32
    # Where the model files (~67 MB) are cached. Blank = the OS temp directory,
    # which many hosts wipe on restart; set a persistent path in production.
    embedding_cache_dir: Optional[str] = None
    embedding_threads: Optional[int] = None
    # Load the model in the background at startup, so the first search does
    # not pay for it. Only happens when meeting search is configured.
    embedding_preload: bool = True

    # Knowledge chunks are far smaller than analysis chunks: retrieval wants a
    # precise passage, not a whole section of the meeting.
    knowledge_chunk_char_size: int = 1200
    knowledge_chunk_overlap_chars: int = 150
    knowledge_max_chunks_per_meeting: int = 200
    knowledge_max_backfill_meetings: int = 200

    # ---------------------------------------- milestone 3: vector database
    pinecone_api_key: Optional[str] = None
    pinecone_index_name: str = "meeting-knowledge"
    pinecone_namespace: str = "meetings"
    pinecone_base_url: str = "https://api.pinecone.io"
    pinecone_index_host: Optional[str] = None   # cached; looked up when blank
    pinecone_timeout_seconds: int = 30
    pinecone_max_attempts: int = 2
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_create_index_if_missing: bool = True

    # ------------------------------------ milestone 3: search / retrieval
    search_default_top_k: int = 8
    search_max_top_k: int = 50
    search_min_score: float = 0.0
    rag_top_k: int = 12
    rag_max_context_chars: int = 12000
    rag_max_meetings_in_context: int = 6
    # Index a meeting automatically once its AI analysis succeeds. Failure here
    # never fails the analysis - the meeting stays indexable on demand.
    knowledge_auto_index: bool = True

    # ------------------------------------------------------------ supabase
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_schema: str = "public"
    supabase_timeout_seconds: int = 30

    # --------------------------------------------------------------- misc
    request_id_header: str = "X-Request-ID"

    # ---------------------------------------------------------- validators
    @field_validator("whisper_language", "temp_dir", "groq_api_key", "groq_reasoning_effort",
                     "embedding_cache_dir", "embedding_threads", "pinecone_api_key",
                     "pinecone_index_host", "supabase_url", "supabase_service_role_key",
                     mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Treat `KEY=` in a .env file as "not configured" rather than "empty string".

        Also treats `KEY=    # some comment` as blank. python-dotenv only strips
        an inline comment when whitespace comes right before the `#`, and the
        spaces after `=` are consumed first - so for a blank value the comment
        itself would otherwise become the value (a Pinecone host of
        "# blank = ...", or a temp directory literally named "# blank = ...").
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("#"):
                return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    # ------------------------------------------------------- derived props
    @property
    def cors_origin_list(self) -> List[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or ["http://localhost:5173"]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def temp_root(self) -> Path:
        """Directory that holds in-flight uploads. Created on demand."""
        base = Path(self.temp_dir) if self.temp_dir else Path(tempfile.gettempdir())
        path = base / "acip-uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def llm_configured(self) -> bool:
        """True when Groq, the only LLM provider, has a key."""
        return self.groq_configured

    # -------------------------------------------- milestone 3 derived props
    @property
    def embeddings_configured(self) -> bool:
        """The local embedding library is installed. No key is involved."""
        from importlib.util import find_spec

        return self.embedding_provider == "fastembed" and find_spec("fastembed") is not None

    @property
    def vector_db_configured(self) -> bool:
        return bool(self.pinecone_api_key)

    @property
    def knowledge_configured(self) -> bool:
        """Milestone 3 needs the embedding model, Pinecone and the database."""
        return self.embeddings_configured and self.vector_db_configured and self.supabase_configured


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Import this, never instantiate `Settings()`."""
    return Settings()
