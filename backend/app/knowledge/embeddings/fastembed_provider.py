"""Local embeddings with fastembed - no API, no key, no quota.

Why local
---------
* **Free and unlimited.** Embedding every transcript passage of every meeting,
  and every search query, costs nothing and uses no provider quota.
* **Fast.** A query embeds in a few milliseconds in-process - no network round
  trip - which leaves most of the three-second search budget for Pinecone.
* **Deployable.** fastembed runs ONNX models on ONNX Runtime; it does *not*
  need PyTorch. ONNX Runtime, ``tokenizers`` and ``huggingface_hub`` are already
  installed for faster-whisper, so this adds very little to the backend.
  (``sentence-transformers`` was rejected: it pulls in PyTorch, roughly 2 GB on
  a Linux host.)

Why ``BAAI/bge-small-en-v1.5``
------------------------------
A strong English retrieval model that is small enough for a CPU server:
384-dimension vectors, a 67 MB quantized download, and ~3 ms per query once
loaded. Change ``EMBEDDING_MODEL`` to any fastembed text model, but set
``EMBEDDING_DIMENSIONS`` to match - the load step refuses a mismatch rather than
letting wrong-sized vectors reach Pinecone.

The model is loaded once per process and shared. Loading happens lazily (or in
the background at startup, see ``EMBEDDING_PRELOAD``), and inference runs in a
worker thread so it never blocks FastAPI's event loop.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import get_logger, get_settings
from app.knowledge.embeddings.base import EmbeddingProvider, clean_for_embedding
from app.utils.errors import EmbeddingRequestError, VectorDimensionMismatchError

logger = get_logger(__name__)

#: One loaded model per (model, cache_dir, threads) for the whole process.
_models: Dict[Tuple[str, Optional[str], Optional[int]], Any] = {}
_load_lock = threading.Lock()

ModelFactory = Callable[..., Any]


def _default_factory(model_name: str, cache_dir: Optional[str], threads: Optional[int]) -> Any:
    # Windows cannot create the symlinks huggingface_hub prefers; the fallback
    # works fine, so silence the warning instead of printing it on every start.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from fastembed import TextEmbedding  # imported lazily on purpose

    return TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=threads)


class FastEmbedProvider(EmbeddingProvider):
    name = "fastembed"
    label = "Local embeddings (fastembed)"

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        dimensions: Optional[int] = None,
        cache_dir: Optional[str] = None,
        threads: Optional[int] = None,
        batch_size: Optional[int] = None,
        model_factory: Optional[ModelFactory] = None,
    ) -> None:
        settings = get_settings()
        self._model_name = model or settings.embedding_model
        self._dimensions = dimensions or settings.embedding_dimensions
        self._cache_dir = cache_dir or settings.embedding_cache_dir
        self._threads = threads or settings.embedding_threads
        self._batch_size = max(1, batch_size or settings.embedding_batch_size)
        self._factory = model_factory
        self._instance: Any = None  # used when a test injects its own factory

    # ---------------------------------------------------------- properties
    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_configured(self) -> bool:
        if self._factory is not None:
            return True
        from importlib.util import find_spec

        return find_spec("fastembed") is not None

    # ---------------------------------------------------------------- embed
    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        cleaned = [clean_for_embedding(text) for text in texts]
        if any(not text for text in cleaned):
            raise EmbeddingRequestError(
                "Refusing to embed an empty passage.",
                code="EMBEDDING_EMPTY_INPUT", status_code=422,
            )
        if not cleaned:
            return []
        vectors = await asyncio.to_thread(self._embed_sync, cleaned)
        logger.info("Embedded %s passage(s) locally with %s", len(vectors), self._model_name)
        return vectors

    async def embed_query(self, text: str) -> List[float]:
        cleaned = clean_for_embedding(text)
        if not cleaned:
            raise EmbeddingRequestError(
                "Refusing to embed an empty query.",
                code="EMBEDDING_EMPTY_INPUT", status_code=422,
            )
        vectors = await asyncio.to_thread(self._embed_sync, [cleaned])
        return vectors[0]

    def preload(self) -> None:
        """Load (and on first run, download) the model now. Safe to call twice."""
        self._load()

    # ------------------------------------------------------------ internals
    def _embed_sync(self, texts: List[str]) -> List[List[float]]:
        model = self._load()
        try:
            raw = list(model.embed(texts, batch_size=self._batch_size))
        except Exception as exc:  # noqa: BLE001 - surface as a controlled error
            raise EmbeddingRequestError(
                "The embedding model failed to process the text.",
                code="EMBEDDING_FAILED", internal=str(exc)[:300],
            ) from exc

        if len(raw) != len(texts):
            raise EmbeddingRequestError(
                "The embedding model returned a different number of vectors than inputs.",
                code="EMBEDDING_MALFORMED",
                internal=f"inputs={len(texts)} vectors={len(raw)}",
            )
        return [self._validated(vector) for vector in raw]

    def _validated(self, vector: Any) -> List[float]:
        """Reject anything that is not a usable vector of the configured size."""
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingRequestError(
                "The embedding model returned a malformed vector.", code="EMBEDDING_MALFORMED",
            ) from exc
        if not values:
            raise EmbeddingRequestError(
                "The embedding model returned an empty vector.", code="EMBEDDING_MALFORMED",
            )
        if len(values) != self._dimensions:
            raise self._dimension_error(len(values))
        return values

    def _dimension_error(self, actual: int) -> VectorDimensionMismatchError:
        return VectorDimensionMismatchError(
            f"The embedding model '{self._model_name}' produces {actual}-dimension vectors, "
            f"but EMBEDDING_DIMENSIONS is {self._dimensions}. Set EMBEDDING_DIMENSIONS="
            f"{actual} (and use a Pinecone index of that dimension).",
            details={"model": self._model_name, "expected": self._dimensions, "actual": actual},
        )

    def _load(self) -> Any:
        """Return the shared model, loading it once per process."""
        if self._factory is not None:
            if self._instance is None:
                self._instance = self._factory(self._model_name, self._cache_dir, self._threads)
            return self._instance

        key = (self._model_name, self._cache_dir, self._threads)
        model = _models.get(key)
        if model is not None:
            return model

        with _load_lock:
            model = _models.get(key)
            if model is not None:
                return model

            self._check_declared_dimensions()
            logger.info("Loading embedding model %s (first run downloads it)...",
                        self._model_name)
            try:
                model = _default_factory(self._model_name, self._cache_dir, self._threads)
            except ImportError as exc:
                raise self.not_configured_error("fastembed is not installed") from exc
            except Exception as exc:  # noqa: BLE001 - download/format failures
                raise EmbeddingRequestError(
                    f"The embedding model '{self._model_name}' could not be loaded. On the "
                    "first run it is downloaded, so check the server's internet access and "
                    "that EMBEDDING_MODEL names a supported fastembed model.",
                    code="EMBEDDING_MODEL_UNAVAILABLE", status_code=503,
                    internal=str(exc)[:300],
                ) from exc

            _models[key] = model
            logger.info("Embedding model %s ready (%s dimensions).",
                        self._model_name, self._dimensions)
            return model

    def _check_declared_dimensions(self) -> None:
        """Fail before downloading anything when the configured size is wrong.

        fastembed publishes each model's dimension, so a mismatch between
        EMBEDDING_MODEL and EMBEDDING_DIMENSIONS is caught with a precise
        message instead of surfacing later as a rejected Pinecone upsert.
        """
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise self.not_configured_error("fastembed is not installed") from exc

        supported = {
            entry.get("model"): entry for entry in TextEmbedding.list_supported_models()
        }
        entry = supported.get(self._model_name)
        if entry is None:
            raise EmbeddingRequestError(
                f"'{self._model_name}' is not a model fastembed supports. Check "
                "EMBEDDING_MODEL in backend/.env (default: BAAI/bge-small-en-v1.5).",
                code="EMBEDDING_MODEL_UNAVAILABLE", status_code=503,
            )
        declared = entry.get("dim")
        if declared and int(declared) != self._dimensions:
            raise self._dimension_error(int(declared))


def reset_model_cache() -> None:
    """Forget loaded models. Used by tests."""
    with _load_lock:
        _models.clear()
