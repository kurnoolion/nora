"""Embedding provider abstraction layer.

Defines the EmbeddingProvider protocol that all embedding integrations
implement. This is the ONLY interface the rest of the codebase uses —
no module outside src/vectorstore/ should import any embedding SDK directly.

## How to add a new embedding provider

1. Create a new file in src/vectorstore/ (e.g., `embedding_openai.py`)
2. Implement a class that satisfies the EmbeddingProvider protocol:

    class OpenAIEmbedder:
        def __init__(self, model: str = "text-embedding-3-large", api_key: str = ""):
            ...

        def embed(self, texts: list[str]) -> list[list[float]]:
            # Call your API, return list of embedding vectors
            ...

        def embed_query(self, text: str) -> list[float]:
            # Some models use a different prefix for queries vs documents.
            # Default implementation just calls embed() on a single text.
            ...

        @property
        def dimension(self) -> int:
            return 3072  # model-specific

        @property
        def model_name(self) -> str:
            return "text-embedding-3-large"

3. No base class inheritance needed — just match the method signatures.
   Python's structural typing (Protocol) handles the rest.

4. To use it, pass your provider instance to any component that
   takes an EmbeddingProvider parameter:

    from src.vectorstore.embedding_openai import OpenAIEmbedder
    embedder = OpenAIEmbedder(api_key="...")
    builder = VectorStoreBuilder(embedder=embedder, store=store, config=config)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for embedding providers.

    Any class with matching methods satisfies this protocol.
    No inheritance required.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors (one per input text).
        """
        ...

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query text.

        Some embedding models use asymmetric encoding — different
        prefixes or representations for documents vs queries. This
        method handles the query side. Implementations that don't
        differentiate can just call embed([text])[0].

        Args:
            text: Query string to embed.

        Returns:
            Embedding vector for the query.
        """
        ...

    @property
    def dimension(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...

    @property
    def model_name(self) -> str:
        """Name of the embedding model (for metadata/logging)."""
        ...
