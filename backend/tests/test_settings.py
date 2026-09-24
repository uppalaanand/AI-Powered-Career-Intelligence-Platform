"""Configuration parsing.

Regression test for a real bug: python-dotenv keeps an inline comment as the
value when the value is blank (``KEY=    # comment``), because the spaces after
``=`` are consumed before comment stripping. That turned TEMP_DIR into a
directory literally named "# blank = OS temp dir..." and the Pinecone host into
an invalid URL.
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings


@pytest.mark.parametrize(
    "field",
    ["temp_dir", "pinecone_index_host", "embedding_cache_dir", "whisper_language",
     "groq_api_key", "pinecone_api_key", "supabase_url"],
)
@pytest.mark.parametrize("raw", ["", "   ", "# blank = default", "    # blank = OS temp dir"])
def test_blank_or_comment_only_values_mean_not_configured(field, raw):
    settings = Settings(_env_file=None, **{field: raw})
    assert getattr(settings, field) is None


def test_real_values_are_kept():
    settings = Settings(_env_file=None, temp_dir="/data/uploads", whisper_language="en")
    assert settings.temp_dir == "/data/uploads"
    assert settings.whisper_language == "en"


def test_a_blank_thread_count_is_not_a_parse_error():
    assert Settings(_env_file=None, embedding_threads="   # default").embedding_threads is None


def test_groq_is_the_only_llm_and_embeddings_need_no_key():
    settings = Settings(_env_file=None, groq_api_key="gsk-test")
    assert settings.llm_configured is True
    assert Settings(_env_file=None).llm_configured is False
    assert settings.embedding_provider == "fastembed"
    assert settings.embedding_dimensions == 384
