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
      - "tei" — TEI's native /embed; single-model-per-instance server,
        client-side batched to match server's --max-client-batch-size

    Provider-specific config:
      - sentence-transformers: `embedding_model`, `embedding_device`,
        `embedding_batch_size`, `normalize_embeddings`
      - ollama: `embedding_model` (Ollama model name like "nomic-embed-text"),
        `normalize_embeddings`, plus optional `extra["ollama_url"]`
        (defaults to http://localhost:11434) and `extra["ollama_timeout_s"]`.
      - tei: `embedding_model` (informational — TEI is single-model per
        instance), `normalize_embeddings`, plus required base URL from
        `NORA_EMBEDDING_BASE_URL` env var or `extra["tei_base_url"]`
        (env var wins). Optional bearer token from `NORA_EMBEDDING_API_KEY`
        or `extra["tei_api_key"]`. Optional `extra["tei_timeout_s"]`,
        `extra["tei_max_batch_size"]` (default 32), and
        `extra["tei_max_input_chars"]` (default 8000).
    """
    provider = (config.embedding_provider or "").strip().lower()

    if provider in ("sentence-transformers", "sentence_transformers", "st", "huggingface", "hf"):
        from core.src.vectorstore.embedding_st import SentenceTransformerEmbedder
        return SentenceTransformerEmbedder(
            model_name=config.embedding_model,
            device=config.embedding_device,
            batch_size=config.embedding_batch_size,
            normalize=config.normalize_embeddings,
        )

    if provider == "ollama":
        import os
        from core.src.vectorstore.embedding_ollama import (
            _DEFAULT_MAX_INPUT_CHARS,
            OllamaEmbedder,
        )
        ollama_url = config.extra.get("ollama_url", "http://localhost:11434")
        # Per-request timeout. Default raised from 60 → 300 for larger
        # embedding models (qwen3-embedding-4B class) on CPU where a
        # single ~8000-char chunk can exceed 60s. Override via
        # `extra.ollama_timeout_s` in config or `NORA_OLLAMA_TIMEOUT_S`
        # env var (config wins if both set).
        env_timeout = os.environ.get("NORA_OLLAMA_TIMEOUT_S")
        default_timeout = int(env_timeout) if env_timeout else 300
        timeout = int(config.extra.get("ollama_timeout_s", default_timeout))
        max_chars = int(
            config.extra.get("ollama_max_input_chars", _DEFAULT_MAX_INPUT_CHARS)
        )
        return OllamaEmbedder(
            model_name=config.embedding_model,
            base_url=ollama_url,
            timeout=timeout,
            normalize=config.normalize_embeddings,
            max_input_chars=max_chars,
        )

    if provider == "tei":
        from core.src.env.config import (
            resolve_embedding_api_key,
            resolve_embedding_base_url,
        )
        from core.src.vectorstore.embedding_tei import (
            _DEFAULT_MAX_INPUT_CHARS,
            TEIEmbedder,
        )
        # Env var wins; fall back to extras so callers can wire via
        # EnvironmentConfig JSON without setting a shell var.
        base_url = resolve_embedding_base_url(
            config_store_value=config.extra.get("tei_base_url"),
        )
        if not base_url:
            raise ValueError(
                "tei embedding provider requires a base URL — set "
                "NORA_EMBEDDING_BASE_URL or VectorStoreConfig.extra['tei_base_url']."
            )
        api_key = resolve_embedding_api_key(
            config_store_value=config.extra.get("tei_api_key"),
        )
        timeout = int(config.extra.get("tei_timeout_s", 60))
        max_batch = int(config.extra.get("tei_max_batch_size", 32))
        max_chars = int(
            config.extra.get("tei_max_input_chars", _DEFAULT_MAX_INPUT_CHARS)
        )
        return TEIEmbedder(
            model_name=config.embedding_model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            normalize=config.normalize_embeddings,
            max_input_chars=max_chars,
            max_batch_size=max_batch,
        )

    raise ValueError(
        f"Unknown embedding_provider {config.embedding_provider!r}. "
        f"Supported: 'sentence-transformers' (aliases: 'huggingface', 'hf', 'st'), "
        f"'ollama', 'tei'."
    )


__all__ = ["make_embedder"]
