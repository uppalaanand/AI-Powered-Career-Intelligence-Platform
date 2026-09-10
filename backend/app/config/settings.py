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

    # ------------------------------------------------ LLM provider fallback
    # Primary first. Unconfigured providers are skipped, not called.
    llm_provider_order: str = "grok,gemini,groq"
    # Requests one provider may spend on a single prompt. 2 = one call plus one
    # controlled retry, and only for transient failures (network / timeout /
    # 5xx). Auth, quota and model errors never retry - the chain moves on.
    llm_provider_max_attempts: int = 2
    # Corrective re-prompts when a provider returns unusable JSON. 2 = one call
    # plus one correction; set to 1 to spend the strict minimum of quota.
    llm_schema_retry_attempts: int = 2
    llm_temperature: float = 0.1

    # ----------------------------------------------------------- grok /xai
    xai_api_key: Optional[str] = None
    xai_model: str = "grok-4-fast"
    xai_base_url: str = "https://api.x.ai/v1"
    xai_timeout_seconds: int = 120
    xai_max_retries: int = 3          # legacy; capped by llm_provider_max_attempts
    xai_temperature: float = 0.1

    # -------------------------------------------------------- google gemini
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: int = 120

    # ------------------------------------------------------------ groq
    groq_api_key: Optional[str] = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: int = 120

    # ------------------------------------------------------------ chunking
    llm_chunk_char_size: int = 9000
    llm_chunk_overlap_chars: int = 600
    llm_max_chunks: int = 40

    # ------------------------------------------------------------ supabase
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_schema: str = "public"
    supabase_timeout_seconds: int = 30

    # --------------------------------------------------------------- misc
    request_id_header: str = "X-Request-ID"

    # ---------------------------------------------------------- validators
    @field_validator("whisper_language", "temp_dir", "xai_api_key", "gemini_api_key",
                     "groq_api_key", "supabase_url", "supabase_service_role_key",
                     mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        """Treat `KEY=` in a .env file as "not configured" rather than "empty string"."""
        if isinstance(value, str) and not value.strip():
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
    def grok_configured(self) -> bool:
        return bool(self.xai_api_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def llm_provider_names(self) -> List[str]:
        """Fallback order, de-duplicated, unknown names dropped.

        Defaults to the documented ``grok -> gemini -> groq`` chain if the
        environment sets something unusable.
        """
        known = ("grok", "gemini", "groq")
        seen: List[str] = []
        for raw in self.llm_provider_order.split(","):
            name = raw.strip().lower()
            if name in known and name not in seen:
                seen.append(name)
        return seen or list(known)

    @property
    def configured_llm_providers(self) -> List[str]:
        """Providers that have a key, in fallback order. Never calls anything."""
        available = {
            "grok": self.grok_configured,
            "gemini": self.gemini_configured,
            "groq": self.groq_configured,
        }
        return [name for name in self.llm_provider_names if available[name]]

    @property
    def llm_configured(self) -> bool:
        """True when at least one AI provider can be tried."""
        return bool(self.configured_llm_providers)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Import this, never instantiate `Settings()`."""
    return Settings()
