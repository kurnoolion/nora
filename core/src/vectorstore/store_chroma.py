"""ChromaDB vector store backend.

Concrete VectorStoreProvider using ChromaDB with persistent storage.
Supports metadata filtering and configurable distance metrics.

ChromaDB is used for PoC; production vector store TBD.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.vectorstore.store_base import QueryResult

logger = logging.getLogger(__name__)

# ChromaDB distance metric mapping
_CHROMA_METRICS = {
    "cosine": "cosine",
    "l2": "l2",
    "ip": "ip",
}


class ChromaDBStore:
    """Vector store backend using ChromaDB.

    Satisfies the VectorStoreProvider protocol.

    Args:
        persist_directory: Path for persistent storage.
        collection_name: Name of the collection.
        distance_metric: One of 'cosine', 'l2', 'ip'.
    """

    def __init__(
        self,
        persist_directory: str = "data/vectorstore",
        collection_name: str = "requirements",
        distance_metric: str = "cosine",
    ) -> None:
        import chromadb

        if distance_metric not in _CHROMA_METRICS:
            raise ValueError(
                f"Unknown distance metric '{distance_metric}'. "
                f"Options: {list(_CHROMA_METRICS.keys())}"
            )

        persist_path = Path(persist_directory)
        persist_path.mkdir(parents=True, exist_ok=True)

        self._client = chromadb.PersistentClient(path=str(persist_path))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": _CHROMA_METRICS[distance_metric]},
        )
        self._collection_name = collection_name
        self._distance_metric = distance_metric

        logger.info(
            f"ChromaDB store: collection='{collection_name}', "
            f"metric='{distance_metric}', persist='{persist_directory}', "
            f"existing docs={self._collection.count()}"
        )

    def add(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Add documents to the collection.

        ChromaDB has a batch size limit (~41666 for large embeddings).
        We batch in groups of 5000 to be safe.
        """
        # ChromaDB requires metadata values to be str, int, float, or bool.
        # Convert list values to JSON strings.
        sanitized = [self._sanitize_metadata(m) for m in metadatas]

        batch_size = 5000
        for i in range(0, len(ids), batch_size):
            end = min(i + batch_size, len(ids))
            self._collection.add(
                ids=ids[i:end],
                embeddings=embeddings[i:end],
                documents=documents[i:end],
                metadatas=sanitized[i:end],
            )

        logger.info(f"Added {len(ids)} documents to collection '{self._collection_name}'")

    def query(
        self,
        query_embedding: list[float],
        n_results: int = 10,
        where: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Query for similar documents with optional metadata filtering."""
        kwargs: dict[str, Any] = {
            "query_embeddings": [query_embedding],
            "n_results": min(n_results, self._collection.count()),
        }

        if where:
            kwargs["where"] = where

        if kwargs["n_results"] == 0:
            return QueryResult()

        results = self._collection.query(**kwargs)

        return QueryResult(
            ids=results["ids"][0] if results["ids"] else [],
            documents=results["documents"][0] if results["documents"] else [],
            metadatas=[
                self._deserialize_metadata(m)
                for m in (results["metadatas"][0] if results["metadatas"] else [])
            ],
            distances=results["distances"][0] if results["distances"] else [],
        )

    @property
    def count(self) -> int:
        return self._collection.count()

    def reset(self) -> None:
        """Delete and recreate the collection."""
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": _CHROMA_METRICS[self._distance_metric]},
        )
        logger.info(f"Reset collection '{self._collection_name}'")

    @staticmethod
    def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Convert non-primitive metadata values to JSON strings.

        ChromaDB only supports str, int, float, bool as metadata values.
        Lists and dicts are serialized as JSON strings.
        """
        sanitized = {}
        for k, v in meta.items():
            if isinstance(v, (list, dict)):
                sanitized[k] = json.dumps(v)
            elif v is None:
                sanitized[k] = ""
            else:
                sanitized[k] = v
        return sanitized

    @staticmethod
    def _deserialize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
        """Reverse _sanitize_metadata — parse JSON strings back to lists/dicts."""
        result = {}
        for k, v in meta.items():
            if isinstance(v, str) and v.startswith(("[", "{")):
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, ValueError):
                    result[k] = v
            else:
                result[k] = v
        return result
