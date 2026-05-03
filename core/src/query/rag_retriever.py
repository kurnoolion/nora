"""Targeted vector RAG retriever (TDD 7.4).

Ranks candidate chunks by similarity, scoped to the candidate set
from graph scoping. Enforces diversity across documents.

Retrieval modes:
  1. **Scoped** — filter by candidate req IDs from graph scoping
  2. **Full** — metadata filters by MNO/release (fallback when graph
     scoping found no specific candidates)

Each mode runs in two flavors:
  - **Pure dense** (default when no BM25 index supplied) — vector
    similarity ranking only.
  - **Hybrid** — dense + BM25 in parallel, fused via Reciprocal Rank
    Fusion (RRF). Lifts queries with specific terms ("T3402",
    "VZ_REQ_X") whose dense embeddings rank just below richer leaf
    chunks. See `bm25_index.py` for the sparse side.
"""

from __future__ import annotations

import logging
from typing import Any

from core.src.query.schema import (
    CandidateSet,
    RetrievedChunk,
    MNOScope,
)
from core.src.query.bm25_index import BM25Index, rrf_fuse
from core.src.query.reranker import MockReranker, Reranker
from core.src.vectorstore.embedding_base import EmbeddingProvider
from core.src.vectorstore.store_base import VectorStoreProvider, QueryResult

logger = logging.getLogger(__name__)


# Per-retriever fanout when fusing — pull this many from each side
# before RRF. Larger than top_k because the fusion may favor chunks
# that rank deeper in one list but appear in both.
_HYBRID_FANOUT_MULT = 3

# RRF weight for the dense side. BM25 weight is per-call (passed by
# QueryPipeline based on query type) — 0.0 = pure dense, 1.0 = equal,
# typical = 0.3-0.5 for queries that benefit from sparse signal. See
# `pipeline._TYPE_BM25_WEIGHT` for the policy.
_DENSE_WEIGHT = 1.0
_DEFAULT_BM25_WEIGHT = 0.5


class RAGRetriever:
    """Retrieves and ranks requirement chunks by similarity.

    Supports both pure-dense retrieval (the default) and hybrid
    dense+sparse retrieval when a `BM25Index` is supplied. Hybrid
    mode is transparent to callers — same `retrieve` signature.
    """

    def __init__(
        self,
        embedder: EmbeddingProvider,
        store: VectorStoreProvider,
        top_k: int = 10,
        diversity_min_per_plan: int = 1,
        bm25_index: BM25Index | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._diversity_min = diversity_min_per_plan
        self._bm25 = bm25_index
        # Reranker runs after RRF fusion, before diversity. None
        # falls back to a Mock (passthrough) so the existing
        # retrieval order is preserved when no reranker is supplied.
        self._reranker = reranker or MockReranker()

    def retrieve(
        self,
        query: str,
        candidates: CandidateSet,
        scopes: list[MNOScope],
        top_k: int | None = None,
        bm25_weight: float | None = None,
        rerank: bool = True,
    ) -> list[RetrievedChunk]:
        """Retrieve and rank chunks for the query.

        Routing:
          - candidates have req IDs → scoped retrieval (filter by req_ids)
          - else → metadata retrieval (filter by MNO/release)

        Within each, BM25 is fused with the dense ranking when an
        index is supplied at construction time AND `bm25_weight > 0`.
        Pure-dense behavior is preserved when bm25_index is None or
        bm25_weight is 0 (back-compat + per-call disable).

        Args:
            query: The original user query text.
            candidates: Candidate set from graph scoping.
            scopes: Resolved MNO/release scopes.
            top_k: Override for number of results.
            bm25_weight: RRF weight for the BM25 side; 0.0 = pure
                dense, 1.0 = equal to dense, typical 0.3–0.5 for
                queries that benefit from sparse signal. None falls
                back to the module default.

        Returns:
            List of RetrievedChunk, ranked by relevance.
        """
        k = top_k or self._top_k
        weight = bm25_weight if bm25_weight is not None else _DEFAULT_BM25_WEIGHT
        # When weight is 0, BM25 contributes nothing — short-circuit
        # to pure dense to skip the BM25 search entirely.
        bm25_active = self._bm25 is not None and weight > 0.0
        candidate_req_ids = candidates.requirement_ids()

        # When a reranker is active, pull a wider pool from retrieval
        # so the cross-encoder has more candidates to reorder.
        # `rerank=False` from the caller (per-query-type gate) skips
        # the reranker entirely. Passthrough MockReranker also skips.
        rerank_active = (
            rerank and not isinstance(self._reranker, MockReranker)
        )
        retrieval_k = k * 2 if rerank_active else k

        if candidate_req_ids:
            chunks = self._retrieve_scoped(
                query, candidate_req_ids, retrieval_k, weight, bm25_active,
            )
        else:
            chunks = self._retrieve_metadata(
                query, scopes, retrieval_k, weight, bm25_active,
            )

        # Cross-encoder rerank — applied to the full retrieval pool
        # before truncation to top_k. Reranker returns the same chunks
        # in a (possibly) different order; we then take top_k.
        if rerank_active and len(chunks) > 1:
            chunks = self._reranker.rerank(query, chunks)
            logger.info(
                f"Reranker reordered {len(chunks)} chunks; keeping top {k}"
            )
            chunks = chunks[:k]

        if self._diversity_min > 0 and len(chunks) > self._diversity_min:
            chunks = self._enforce_diversity(chunks, k)

        return chunks

    # ── Routing per scope: scoped vs metadata; hybrid vs pure-dense ──

    def _retrieve_scoped(
        self,
        query: str,
        req_ids: list[str],
        top_k: int,
        bm25_weight: float,
        bm25_active: bool,
    ) -> list[RetrievedChunk]:
        """Scoped retrieval: candidate req_ids gate both retrievers."""
        query_embedding = self._embedder.embed_query(query)
        if not bm25_active:
            chunks = self._scoped_retrieve(query_embedding, req_ids, top_k)
            logger.info(
                f"Scoped retrieval (dense): {len(req_ids)} candidates "
                f"→ {len(chunks)} retrieved"
            )
            return chunks
        # Hybrid: pull fanout*top_k from each side, fuse via RRF.
        # The dense path filters by metadata.req_id $in [req_ids];
        # the BM25 path must use the same gate so the populations
        # match. chunk_id and metadata.req_id are not always the
        # same string (chunk_id is often "req:<req_id>"), so we
        # filter via metadata, not chunk_id.
        fanout = top_k * _HYBRID_FANOUT_MULT
        dense_chunks = self._scoped_retrieve(query_embedding, req_ids, fanout)
        bm25_pairs = self._bm25.search(
            query, top_k=fanout, filter_metadata=("req_id", set(req_ids)),
        )
        chunks = self._fuse(dense_chunks, bm25_pairs, top_k, bm25_weight)
        logger.info(
            f"Scoped retrieval (hybrid w={bm25_weight}): "
            f"{len(req_ids)} candidates → dense={len(dense_chunks)} "
            f"bm25={len(bm25_pairs)} fused={len(chunks)}"
        )
        return chunks

    def _retrieve_metadata(
        self,
        query: str,
        scopes: list[MNOScope],
        top_k: int,
        bm25_weight: float,
        bm25_active: bool,
    ) -> list[RetrievedChunk]:
        """Metadata-filtered retrieval — used when graph scoping is empty."""
        query_embedding = self._embedder.embed_query(query)
        if not bm25_active:
            chunks = self._metadata_retrieve(query_embedding, scopes, top_k)
            logger.info(
                f"Metadata retrieval (dense, no candidates): "
                f"{len(chunks)} retrieved"
            )
            return chunks
        # Hybrid: BM25 doesn't know MNO/release — get the dense path's
        # MNO/release-filtered ids and use them as BM25's filter_ids.
        # That keeps the fused population identical between sides.
        fanout = top_k * _HYBRID_FANOUT_MULT
        dense_chunks = self._metadata_retrieve(query_embedding, scopes, fanout)
        in_scope_ids = {c.chunk_id for c in dense_chunks}
        bm25_pairs = self._bm25.search(
            query, top_k=fanout, filter_ids=in_scope_ids,
        )
        chunks = self._fuse(dense_chunks, bm25_pairs, top_k, bm25_weight)
        logger.info(
            f"Metadata retrieval (hybrid w={bm25_weight}): "
            f"dense={len(dense_chunks)} bm25={len(bm25_pairs)} "
            f"fused={len(chunks)}"
        )
        return chunks

    def _fuse(
        self,
        dense_chunks: list[RetrievedChunk],
        bm25_pairs: list[tuple[str, float]],
        top_k: int,
        bm25_weight: float,
    ) -> list[RetrievedChunk]:
        """RRF-fuse dense and BM25 rankings; materialize chunks for
        BM25-only ids using the BM25 index's text/metadata cache.
        """
        dense_ids = [c.chunk_id for c in dense_chunks]
        bm25_ids = [cid for cid, _ in bm25_pairs]
        fused = rrf_fuse(
            dense_ids, bm25_ids,
            weights=[_DENSE_WEIGHT, bm25_weight],
            top_k=top_k,
        )

        # Build a lookup for materialization. Dense chunks are ready;
        # BM25-only ids need text + metadata from the BM25 index.
        dense_by_id = {c.chunk_id: c for c in dense_chunks}
        out: list[RetrievedChunk] = []
        for cid, fused_score in fused:
            if cid in dense_by_id:
                # Preserve the dense distance for downstream callers
                # that look at it; fused_score is informational only.
                out.append(dense_by_id[cid])
            elif self._bm25 is not None:
                out.append(RetrievedChunk(
                    chunk_id=cid,
                    text=self._bm25.chunk_text(cid),
                    metadata=self._bm25.chunk_metadata(cid),
                    similarity_score=0.0,  # no dense distance
                    graph_node_id=cid,
                ))
        return out

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
