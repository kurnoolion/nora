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

from src.query.analyzer import MockQueryAnalyzer
from src.query.resolver import MNOReleaseResolver
from src.query.graph_scope import GraphScoper
from src.query.rag_retriever import RAGRetriever
from src.query.context_builder import ContextBuilder
from src.query.synthesizer import MockSynthesizer
from src.query.schema import QueryResponse, CandidateSet

from src.vectorstore.embedding_base import EmbeddingProvider
from src.vectorstore.store_base import VectorStoreProvider

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
        """
        self._analyzer = analyzer or MockQueryAnalyzer()
        self._resolver = MNOReleaseResolver(graph)
        self._scoper = GraphScoper(graph, max_depth=max_depth)
        self._retriever = RAGRetriever(embedder, store, top_k=top_k)
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

        # Stage 4: Targeted RAG
        chunks = self._retriever.retrieve(
            query_text, candidates, scoped.scoped_mnos, top_k=self._top_k,
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
