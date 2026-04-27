"""Vector store abstraction layer.

Defines the VectorStoreProvider protocol that all vector store backends
implement. Supports metadata-filtered similarity search.

## How to add a new vector store backend

1. Create a new file in src/vectorstore/ (e.g., `store_faiss.py`)
2. Implement a class that satisfies the VectorStoreProvider protocol:

    class FAISSStore:
        def __init__(self, persist_dir: str, dimension: int):
            ...

        def add(self, ids, embeddings, documents, metadatas):
            ...

        def query(self, query_embedding, n_results, where):
            ...

        @property
        def count(self):
            ...

        def reset(self):
            ...

3. No base class inheritance needed — structural typing via Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class QueryResult:
    """Result from a vector store query.

    Attributes:
        ids: List of chunk IDs, ordered by relevance.
        documents: List of chunk texts (same order).
        metadatas: List of metadata dicts (same order).
        distances: List of distance/similarity scores (same order).
    """
    ids: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    metadatas: list[dict[str, Any]] = field(default_factory=list)
    distances: list[float] = field(default_factory=list)


@runtime_checkable
class VectorStoreProvider(Protocol):
    """Protocol for vector store backends.

    Any class with matching methods satisfies this protocol.
    No inheritance required.
    """

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Add documents with their embeddings and metadata to the store.

        Args:
            ids: Unique identifiers for each document.
            embeddings: Pre-computed embedding vectors.
            documents: Original text content.
            metadatas: Metadata dicts for filtering.
        """
        ...

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Query the store for similar documents.

        Args:
            query_embedding: Query vector to search for.
            n_results: Maximum number of results.
            where: Metadata filter (backend-specific syntax).

        Returns:
            QueryResult with ids, documents, metadatas, distances.
        """
        ...

    @property
    def count(self) -> int:
        """Number of documents in the store."""
        ...

    def reset(self) -> None:
        """Delete all documents from the store."""
        ...
