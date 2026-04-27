"""Targeted vector RAG retriever (TDD 7.4).

Ranks candidate chunks by vector similarity, scoped to the
candidate set from graph scoping. Enforces diversity across
documents.

Two modes:
  1. Scoped retrieval — query only against candidate req IDs
  2. Full retrieval — query entire store with metadata filters
     (fallback when no candidates from graph scoping)
"""

from __future__ import annotations

import logging
from typing import Any

from src.query.schema import (
    CandidateSet,
    RetrievedChunk,
    MNOScope,
)
from src.vectorstore.embedding_base import EmbeddingProvider
from src.vectorstore.store_base import VectorStoreProvider, QueryResult

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Retrieves and ranks requirement chunks by vector similarity."""

    def __init__(
        self,
        embedder: EmbeddingProvider,
        store: VectorStoreProvider,
        top_k: int = 10,
        diversity_min_per_plan: int = 1,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._diversity_min = diversity_min_per_plan

    def retrieve(
        self,
        query: str,
        candidates: CandidateSet,
        scopes: list[MNOScope],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """Retrieve and rank chunks for the query.

        If candidates have requirement nodes, uses scoped retrieval
        (metadata filter by req_ids). Otherwise falls back to
        metadata-filtered retrieval by MNO/release.

        Args:
            query: The original user query text.
            candidates: Candidate set from graph scoping.
            scopes: Resolved MNO/release scopes.
            top_k: Override for number of results.

        Returns:
            List of RetrievedChunk, ranked by relevance.
        """
        k = top_k or self._top_k

        # Embed the query
        query_embedding = self._embedder.embed_query(query)

        # Determine retrieval strategy
        candidate_req_ids = candidates.requirement_ids()

        if candidate_req_ids:
            # Scoped retrieval: query filtered to candidate req IDs
            chunks = self._scoped_retrieve(
                query_embedding, candidate_req_ids, k
            )
            logger.info(
                f"Scoped retrieval: {len(candidate_req_ids)} candidates → "
                f"{len(chunks)} retrieved"
            )
        else:
            # Fallback: metadata-filtered retrieval
            chunks = self._metadata_retrieve(query_embedding, scopes, k)
            logger.info(
                f"Metadata retrieval (no graph candidates): "
                f"{len(chunks)} retrieved"
            )

        # Apply diversity enforcement
        if self._diversity_min > 0 and len(chunks) > self._diversity_min:
            chunks = self._enforce_diversity(chunks, k)

        return chunks

    def _scoped_retrieve(
        self,
        query_embedding: list[float],
        req_ids: list[str],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Retrieve from the vector store filtered to specific req_ids.

        ChromaDB $in operator supports filtering by a list of values.
        If the list is too large, we fall back to retrieving more and
        filtering client-side.
        """
        # ChromaDB supports $in for list filtering
        if len(req_ids) <= 500:
            where = {"req_id": {"$in": req_ids}}
            result = self._store.query(
                query_embedding, n_results=top_k, where=where
            )
            return self._to_chunks(result)
        else:
            # Large candidate set — retrieve more and filter client-side
            result = self._store.query(
                query_embedding, n_results=top_k * 3
            )
            chunks = self._to_chunks(result)
            req_id_set = set(req_ids)
            filtered = [
                c for c in chunks
                if c.metadata.get("req_id", "") in req_id_set
            ]
            return filtered[:top_k]

    def _metadata_retrieve(
        self,
        query_embedding: list[float],
        scopes: list[MNOScope],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Retrieve with MNO/release metadata filters."""
        if len(scopes) == 1:
            where = {
                "$and": [
                    {"mno": scopes[0].mno},
                    {"release": scopes[0].release},
                ]
            }
        elif len(scopes) > 1:
            # Multiple scopes — use $or
            where = {
                "$or": [
                    {"$and": [{"mno": s.mno}, {"release": s.release}]}
                    for s in scopes
                ]
            }
        else:
            where = None

        result = self._store.query(
            query_embedding, n_results=top_k, where=where
        )
        return self._to_chunks(result)

    def _enforce_diversity(
        self,
        chunks: list[RetrievedChunk],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Ensure at least N chunks from each contributing plan.

        Strategy: take the top chunks by score, but ensure at least
        diversity_min from each plan that appears in the candidates.
        """
        # Group by plan
        by_plan: dict[str, list[RetrievedChunk]] = {}
        for chunk in chunks:
            pid = chunk.metadata.get("plan_id", "unknown")
            by_plan.setdefault(pid, []).append(chunk)

        # Guarantee minimum per plan
        selected: list[RetrievedChunk] = []
        selected_ids: set[str] = set()

        for pid, plan_chunks in by_plan.items():
            for chunk in plan_chunks[: self._diversity_min]:
                if chunk.chunk_id not in selected_ids:
                    selected.append(chunk)
                    selected_ids.add(chunk.chunk_id)

        # Fill remaining slots from the ranked list
        for chunk in chunks:
            if len(selected) >= top_k:
                break
            if chunk.chunk_id not in selected_ids:
                selected.append(chunk)
                selected_ids.add(chunk.chunk_id)

        # Re-sort by similarity score
        selected.sort(key=lambda c: c.similarity_score)

        return selected

    @staticmethod
    def _to_chunks(result: QueryResult) -> list[RetrievedChunk]:
        """Convert a QueryResult to a list of RetrievedChunk."""
        chunks = []
        for i, chunk_id in enumerate(result.ids):
            chunks.append(RetrievedChunk(
                chunk_id=chunk_id,
                text=result.documents[i] if i < len(result.documents) else "",
                metadata=result.metadatas[i] if i < len(result.metadatas) else {},
                similarity_score=result.distances[i] if i < len(result.distances) else 0.0,
                graph_node_id=chunk_id,  # chunk_id = "req:<req_id>" = graph node id
            ))
        return chunks
