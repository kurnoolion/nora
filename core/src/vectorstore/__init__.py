"""Vector store module — embedding + storage Protocols + provider factory.

The Protocols (`EmbeddingProvider`, `VectorStoreProvider`) and concrete
implementations live in their own modules; this `__init__` exposes the
`make_embedder()` factory so callers (e.g. pipeline stages, eval runner)
don't have to know which provider class to import.

Per D-007: providers are swappable by instance. The factory just routes
`VectorStoreConfig.embedding_provider` to the right constructor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from core.src.vectorstore.config import VectorStoreConfig
    from core.src.vectorstore.embedding_base import EmbeddingProvider


def make_embedder(config: "VectorStoreConfig") -> "EmbeddingProvider":
    """Construct the embedder named by `config.embedding_provider`.

    Supported providers:
      - "sentence-transformers" (default) — local HF-cached model, fast batch encoding
      - "ollama" — Ollama's /api/embeddings; same offline distribution as the
        Ollama LLM (no separate HuggingFace cache needed)

    Provider-specific config:
      - sentence-transformers: `embedding_model`, `embedding_device`,
        `embedding_batch_size`, `normalize_embeddings`
      - ollama: `embedding_model` (Ollama model name like "nomic-embed-text"),
        `normalize_embeddings`, plus optional `extra["ollama_url"]`
        (defaults to http://localhost:11434) and `extra["ollama_timeout_s"]`.
    """
    provider = (config.embedding_provider or "").strip().lower()

    if provider in ("sentence-transformers", "sentence_transformers", "st"):
        from core.src.vectorstore.embedding_st import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder(
            model_name=config.embedding_model,
            device=config.embedding_device,
            batch_size=config.embedding_batch_size,
            normalize=config.normalize_embeddings,
        )

    if provider == "ollama":
        from core.src.vectorstore.embedding_ollama import OllamaEmbedder
        ollama_url = config.extra.get("ollama_url", "http://localhost:11434")
        timeout = int(config.extra.get("ollama_timeout_s", 60))
        return OllamaEmbedder(
            model_name=config.embedding_model,
            base_url=ollama_url,
            timeout=timeout,
            normalize=config.normalize_embeddings,
        )

    raise ValueError(
        f"Unknown embedding_provider {config.embedding_provider!r}. "
        f"Supported: 'sentence-transformers', 'ollama'."
    )


__all__ = ["make_embedder"]
