"""Query pipeline orchestrator (TDD 7).

Wires the 6 stages together:
  1. Query Analysis → QueryIntent
  2. MNO/Release Resolution → ScopedQuery
  3. Graph Scoping → CandidateSet
  4. Targeted RAG → [RetrievedChunk]
  5. Context Assembly → AssembledContext
  6. LLM Synthesis → QueryResponse

All components are injected — the pipeline works with any
combination of analyzer, embedder, store, LLM, and graph.
"""

from __future__ import annotations

import logging
from pathlib import Path

import networkx as nx

from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.resolver import MNOReleaseResolver
from core.src.query.graph_scope import GraphScoper
from core.src.query.rag_retriever import RAGRetriever
from core.src.query.context_builder import ContextBuilder
from core.src.query.synthesizer import MockSynthesizer
from core.src.query.schema import QueryResponse, CandidateSet, QueryType


# Per-query-type retrieval breadth. Lookup-style queries ("What is
# VZ_REQ_X?") work best with a tight top_k anchored on the entity
# match. List/breadth-style queries ("What requirements exist for X
# across all specs?") need more headroom because the expected hits
# are often parent/overview reqs whose chunks are short (heading +
# path only) and rank just below richer leaf chunks; widening top_k
# lets those surface.
_TYPE_TOP_K = {
    QueryType.CROSS_DOC: 25,
    QueryType.CROSS_MNO_COMPARISON: 25,
    QueryType.STANDARDS_COMPARISON: 25,
    QueryType.FEATURE_LEVEL: 25,
    QueryType.TRACEABILITY: 20,
    QueryType.RELEASE_DIFF: 20,
}

# Per-query-type BM25 weight in the RRF fusion. 0.0 disables BM25 for
# that query type (pure dense retrieval). Empirical tuning on the OA
# eval set:
#   - STANDARDS_COMPARISON gains +33pp accuracy with BM25 active
#     (queries name specific TS numbers / cause codes that BM25 weights
#     heavily but dense embeddings spread thin).
#   - CROSS_DOC and FEATURE_LEVEL regress when BM25 contributes — the
#     expected hits are thin parent/overview chunks that BM25 ranks
#     low; richer leaf chunks pulled up by BM25 displace them.
#   - TRACEABILITY benefits modestly when the query names a specific
#     req_id (entity-priority graph scoping handles the well-formed
#     case in D-039; BM25 helps with concept-shaped trace queries).
# Numbers may shift as the eval set grows; treat as tuning, not contract.
_TYPE_BM25_WEIGHT = {
    QueryType.STANDARDS_COMPARISON: 0.5,
    QueryType.TRACEABILITY: 0.5,
    QueryType.SINGLE_DOC: 0.5,
    # CROSS_DOC, FEATURE_LEVEL, CROSS_MNO_COMPARISON, RELEASE_DIFF,
    # GENERAL: omitted → default 0.0 (pure dense)
}

from core.src.vectorstore.embedding_base import EmbeddingProvider
from core.src.vectorstore.store_base import VectorStoreProvider

logger = logging.getLogger(__name__)


class QueryPipeline:
    """End-to-end query pipeline.

    Usage:
        pipeline = QueryPipeline(graph, embedder, store)
        response = pipeline.query("What is the T3402 timer behavior?")
    """

    def __init__(
        self,
        graph: nx.DiGraph,
        embedder: EmbeddingProvider,
        store: VectorStoreProvider,
        analyzer=None,
        synthesizer=None,
        top_k: int = 10,
        max_depth: int | None = None,
        max_context_chars: int = 30000,
        enable_bm25: bool = True,
    ) -> None:
        """Initialize the pipeline.

        Args:
            graph: Knowledge graph (NetworkX DiGraph).
            embedder: Embedding provider for query embedding.
            store: Vector store for chunk retrieval.
            analyzer: Query analyzer (default: MockQueryAnalyzer).
            synthesizer: LLM synthesizer (default: MockSynthesizer).
            top_k: Number of chunks to retrieve.
            max_depth: Override graph traversal depth.
            max_context_chars: Maximum context length for LLM.
            enable_bm25: When True (default), build a BM25 sparse index
                from the store at construction time and fuse it with
                dense retrieval via RRF. False keeps the legacy pure-
                dense path. Build cost is O(n) over chunks; memory cost
                is the chunk corpus once over (text + tokenized list).
        """
        from core.src.query.bm25_index import BM25Index

        self._analyzer = analyzer or MockQueryAnalyzer()
        self._resolver = MNOReleaseResolver(graph)
        self._scoper = GraphScoper(graph, max_depth=max_depth)
        bm25_index = BM25Index.from_store(store) if enable_bm25 else None
        self._retriever = RAGRetriever(
            embedder, store, top_k=top_k, bm25_index=bm25_index
        )
        self._context_builder = ContextBuilder(graph)
        self._synthesizer = synthesizer or MockSynthesizer()
        self._top_k = top_k
        self._max_context_chars = max_context_chars
        self._bypass_graph = False

    def query(self, query_text: str, verbose: bool = False) -> QueryResponse:
        """Run the full query pipeline.

        Args:
            query_text: Natural language query.
            verbose: If True, log intermediate results.

        Returns:
            QueryResponse with answer and citations.
        """
        # Stage 1: Query Analysis
        intent = self._analyzer.analyze(query_text)
        if verbose:
            logger.info(f"[Stage 1] Intent: {intent.to_dict()}")

        # Stage 2: MNO/Release Resolution
        scoped = self._resolver.resolve(intent)
        if verbose:
            logger.info(f"[Stage 2] Scope: {scoped.to_dict()}")

        # Stage 3: Graph Scoping
        if self._bypass_graph:
            candidates = CandidateSet()
            if verbose:
                logger.info("[Stage 3] BYPASSED (pure RAG mode)")
        else:
            candidates = self._scoper.scope(scoped)
            if verbose:
                logger.info(f"[Stage 3] Candidates: {candidates.to_dict()}")

        # Stage 4: Targeted RAG.
        # `top_k` widens for cross-doc / list-style query types — the
        # expected hits in those categories are often parent/overview
        # chunks that rank below the richer leaf chunks, so a tight
        # top_k systematically misses them.
        # `bm25_weight` is per-query-type (0.0 = pure dense). See
        # `_TYPE_BM25_WEIGHT` for the rationale; empirical tuning on
        # OA eval found BM25 helps standards / traceability / single-
        # doc queries but hurts cross-doc / feature-level (parent
        # chunks too thin to compete with BM25-favored richer chunks).
        type_top_k = max(self._top_k, _TYPE_TOP_K.get(intent.query_type, 0))
        bm25_weight = _TYPE_BM25_WEIGHT.get(intent.query_type, 0.0)
        chunks = self._retriever.retrieve(
            query_text, candidates, scoped.scoped_mnos,
            top_k=type_top_k, bm25_weight=bm25_weight,
        )
        if verbose:
            logger.info(
                f"[Stage 4] Retrieved: {len(chunks)} chunks "
                f"from {len(set(c.metadata.get('plan_id','') for c in chunks))} plans"
            )

        # Stage 5: Context Assembly
        context = self._context_builder.build(
            query_text, chunks, intent.query_type,
            max_context_chars=self._max_context_chars,
        )
        if verbose:
            logger.info(
                f"[Stage 5] Context: {len(context.context_text)} chars, "
                f"{len(context.chunks)} chunks"
            )

        # Stage 6: LLM Synthesis
        response = self._synthesizer.synthesize(context, intent)
        response.candidate_count = candidates.total

        if verbose:
            logger.info(
                f"[Stage 6] Response: {len(response.answer)} chars, "
                f"{len(response.citations)} citations"
            )

        return response


def load_graph(graph_path: Path) -> nx.DiGraph:
    """Load a knowledge graph from JSON."""
    import json
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    graph = nx.node_link_graph(data)
    logger.info(
        f"Loaded graph: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges"
    )
    return graph
