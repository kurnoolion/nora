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
from core.src.query.rewriter import MockQueryRewriter, expand_query
from core.src.query.schema import QueryResponse, CandidateSet, QueryType

# "Not found" answer returned when every retrieved chunk scores below the
# quality bar set by max_distance_threshold. The pipeline returns this
# instead of synthesizing from weak fragments, which is the primary
# hallucination class for out-of-scope queries.
_NOT_FOUND_ANSWER = (
    "No matching requirements were found in the indexed corpus for this query. "
    "The query may reference a topic, feature, or MNO release that is not yet "
    "ingested, or the phrasing may need to be adjusted. "
    "Try rephrasing, specifying an MNO/release, or checking which documents "
    "have been indexed."
)

# Returned when Stage 4.7 finds multiple plausible groups whose scores
# are within `gap_threshold` of each other — the pipeline cannot pick a
# winner, and synthesizing across all groups would conflate distinct
# topics. The QueryResponse carries `disambiguation_required=True` and
# the populated `groups` list; the UI renders them as user choices.
_DISAMBIGUATION_ANSWER = (
    "The query matched multiple distinct sections of the corpus that scored "
    "similarly. Synthesizing across them would conflate topics, so the "
    "system is asking you to pick which group to answer from. "
    "Each group's hierarchy path and a few representative section titles "
    "are listed below. Pick one to synthesize, or refine your query to be "
    "more specific."
)

# Per-query-type distance threshold overrides. When a query type is
# listed here, its threshold takes precedence over the pipeline-level
# default. FACT enforces a stricter cap than the general default
# because a fact-shaped answer that synthesizes from weak chunks is
# the worst-case hallucination — it looks authoritative but is wrong.
# SUMMARIZE intentionally uses a *looser* threshold (or unset) so
# breadth queries can scoop up parent/overview chunks at higher
# distances that would otherwise be filtered.
_TYPE_MAX_DISTANCE: dict[QueryType, float] = {
    QueryType.FACT: 0.4,
    QueryType.SUMMARIZE: 0.7,
}


# Per-query-type retrieval breadth is resolved via env/config.resolve_top_k.
# Two-bucket design — each QueryType maps to broad (default 25, env var
# NORA_BROAD_QUERY_TOP_K) or narrow (default 10, NORA_NARROW_QUERY_TOP_K),
# with DB override via the Config page. The bucket assignment lives in
# env.config._BROAD_QUERY_TYPES; queries not in that set are narrow.

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
    # FACT queries name specific timer/threshold/value tokens; BM25
    # weight matches them exactly while dense embeddings spread
    # similar phrasing too thin.
    QueryType.FACT: 0.5,
    # SUMMARIZE leans dense — the user paraphrases the topic, so
    # semantic recall matters more than exact-token match.
    QueryType.SUMMARIZE: 0.2,
    # CROSS_DOC, FEATURE_LEVEL, CROSS_MNO_COMPARISON, RELEASE_DIFF,
    # GENERAL: omitted → default 0.0 (pure dense)
}

# Per-query-type rewrite enable. SINGLE_DOC lookups already work via
# entity-priority graph scoping (D-039) so query rewriting adds noise
# without recall benefit. Concept-shaped queries (CROSS_DOC,
# STANDARDS_COMPARISON, FEATURE_LEVEL, etc.) lack specific terms that
# embedding/BM25 can grab onto — rewriting injects telecom-specific
# terminology (acronym ↔ expansion, NAS message names, spec citations)
# that lifts retrieval recall.
_TYPE_REWRITE_ENABLED = {
    QueryType.CROSS_DOC: True,
    QueryType.STANDARDS_COMPARISON: True,
    QueryType.FEATURE_LEVEL: True,
    QueryType.CROSS_MNO_COMPARISON: True,
    QueryType.TRACEABILITY: True,
    QueryType.RELEASE_DIFF: True,
    # SUMMARIZE benefits from term expansion — paraphrases bring in
    # related concepts the user didn't name.
    QueryType.SUMMARIZE: True,
    # FACT explicitly disabled — paraphrasing a "what is the value of
    # T3402" query risks the rewriter substituting "what is T3402"
    # which routes to the acronym/definitional path instead of fact-
    # value retrieval.
    # SINGLE_DOC, GENERAL: omitted → default False
}

# Cross-encoder reranking enable per query-type. Reranker reorders
# the fused top-K from BM25 + dense retrieval using a small cross-
# encoder model that scores (query, chunk) pairs jointly. Most
# expensive of the retrieval-quality knobs but generally the
# highest-quality reordering signal.
#
# Default: enabled for ALL query types when reranker_enabled is True.
# Previously gated to broad/concept query types only based on
# pre-strand empirical tuning; the
# nora-retrieval-parent-displacement strand reverted that gating
# because GENERAL-classified queries were silently skipping rerank
# and producing the same ordering as raw retrieval. Per-type override
# via this dict still possible — set an entry to False to skip rerank
# for that type (e.g. SUMMARIZE if per-chunk cost dominates).
_TYPE_RERANK_ENABLED: dict[QueryType, bool] = {
    # No explicit entries → all types get the .get() default below,
    # which is True. Add `QueryType.X: False` here to selectively
    # disable rerank for a specific type.
}

# Default applied by .get(qt, _DEFAULT_RERANK_ENABLED) when a
# QueryType isn't in _TYPE_RERANK_ENABLED. Set True so a globally-
# enabled reranker actually fires on every query.
_DEFAULT_RERANK_ENABLED: bool = True

# Query types that bypass Stage 4.7 grouping even when grouping is
# globally enabled. SUMMARIZE intent inherently wants ALL groups
# merged into one synthesis pass — picking any single group throws
# away the breadth the user is asking for. Adding a type here is a
# semantic statement: "this intent is incompatible with auto-commit-
# to-one-group behavior."
_TYPE_DISABLE_GROUPING: set[QueryType] = {
    QueryType.SUMMARIZE,
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
        rewriter=None,
        reranker=None,
        top_k: int = 10,
        max_depth: int | None = None,
        max_context_chars: int = 30000,
        enable_bm25: bool = True,
        max_distance_threshold: float | None = None,
        enable_grouping: bool = False,
        top_k_cap: int | None = None,
        cell_stores: "dict | None" = None,
    ) -> None:
        """Initialize the pipeline.

        Args:
            graph: Knowledge graph (NetworkX DiGraph).
            embedder: Embedding provider for query embedding.
            store: Vector store for chunk retrieval.
            analyzer: Query analyzer (default: MockQueryAnalyzer).
            synthesizer: LLM synthesizer (default: MockSynthesizer).
            rewriter: Optional pre-retrieval query rewriter
                (default: MockQueryRewriter — no rewrites). Pass an
                `LLMQueryRewriter(llm_provider)` for production.
            top_k: Number of chunks to retrieve.
            max_depth: Override graph traversal depth.
            max_context_chars: Maximum context length for LLM.
            enable_bm25: When True (default), build a BM25 sparse index
                from the store at construction time and fuse it with
                dense retrieval via RRF. False keeps the legacy pure-
                dense path. Build cost is O(n) over chunks; memory cost
                is the chunk corpus once over (text + tokenized list).
            max_distance_threshold: Maximum cosine distance for a chunk
                to be considered relevant. Chunks with
                similarity_score > threshold are discarded. When all
                retrieved chunks are discarded, the pipeline returns
                _NOT_FOUND_ANSWER instead of synthesizing from weak
                fragments. None (default) disables the filter — all
                retrieved chunks are passed to synthesis.
                Typical values: 0.5 (lenient), 0.35 (moderate),
                0.25 (strict). Tune against your embedding model and
                corpus; cosine distances are in [0, 2] with 0 = identical.
            enable_grouping: When True, Stage 4.7 clusters retrieved
                chunks by hierarchy_path (D-046) after the threshold
                filter. If groups are clearly separated (gap > the
                resolved gap threshold), the pipeline auto-commits to
                the top group and synthesizes from those chunks only.
                If groups are close (gap < threshold), the pipeline
                short-circuits and returns a disambiguation
                QueryResponse for the UI to surface. False (default)
                preserves pre-Step-3 behavior. Gap threshold is
                resolved per query via the unified config chain
                (CLI > NORA_RETRIEVAL_GAP_THRESHOLD > config/retrieval.json
                gap_threshold_by_type[<query_type>] > config scalar
                > built-in default 0.05).
            top_k_cap: Hard upper limit on chunks retrieved per query.
                Applied AFTER per-bucket widening (resolve_top_k
                returns broad=25 / narrow=10 by default; cap to 5
                forces min(25, 5)=5 even for broad queries). None
                (default) = no cap. `top_k` is no longer used as a
                floor since resolve_top_k always returns a positive
                value; it's retained as a constructor parameter for
                callers that still pass it.
        """
        from core.src.query.bm25_index import BM25Index
        from core.src.query.reranker import MockReranker
        from core.src.vectorstore.cell_loader import FLAT_CELL

        self._analyzer = analyzer or MockQueryAnalyzer()
        self._resolver = MNOReleaseResolver(graph)
        self._scoper = GraphScoper(graph, max_depth=max_depth)
        # D-DRAFT-11: one retriever per (mno, release) cell. A single flat
        # `store` (legacy / single-cell) normalizes to {FLAT_CELL: store}, so
        # existing callers are unchanged. `cell_stores` (from
        # vectorstore.load_cell_stores) drives multi-cell routing + fusion.
        if cell_stores is None:
            cell_stores = {FLAT_CELL: store}
        self._reranker = reranker or MockReranker()
        self._retrievers: dict = {}
        for ck, st in cell_stores.items():
            bm25 = BM25Index.from_store(st) if enable_bm25 else None
            self._retrievers[ck] = RAGRetriever(
                embedder, st, top_k=top_k, bm25_index=bm25, reranker=reranker,
            )
        self._stores = list(cell_stores.values())
        self._context_builder = ContextBuilder(graph)
        self._synthesizer = synthesizer or MockSynthesizer()
        self._rewriter = rewriter or MockQueryRewriter()
        self._top_k = top_k
        self._max_context_chars = max_context_chars
        self._bypass_graph = False
        self._max_distance_threshold = max_distance_threshold
        self._enable_grouping = enable_grouping
        self._top_k_cap = top_k_cap
        # Representative store for back-compat paths; multi-cell pinned-chunk
        # fetch walks all stores (see _fetch_chunks_by_ids).
        self._store = self._stores[0] if self._stores else store

    def _select_cells(self, scoped) -> list:
        """Target cell keys for a query (D-DRAFT-11).

        The resolver already produced `(mno, release)` scope pairs
        (`scoped.scoped_mnos`, each an `MNOScope`), which map directly to cell
        keys. Cells present in the pipeline that match are selected; if none
        match (e.g. a single flat `FLAT_CELL` store, or an unresolved scope),
        fall back to all loaded cells.
        """
        target = [
            (s.mno, s.release) for s in scoped.scoped_mnos
            if (s.mno, s.release) in self._retrievers
        ]
        return target or list(self._retrievers.keys())

    def _fetch_chunks_by_ids(self, ids: list[str]) -> list:
        """Fetch chunks from the store by their chunk IDs.

        Used by the pinned-chunks path. The protocol doesn't expose a
        get-by-ids method, so we walk store.get_all() and filter — O(n)
        on store size, fine for ~1k-chunk corpora. Promote to a typed
        store method if performance ever matters.

        Returns: list of RetrievedChunk in the same order as requested
        IDs (skipping unknowns). similarity_score is set to 0.0 since
        the user explicitly picked these — no ranking required.
        """
        from core.src.query.schema import RetrievedChunk

        # Multi-cell (D-DRAFT-11): a pinned chunk may live in any cell store —
        # walk them all and union by id (first store wins on a duplicate id).
        by_id: dict = {}
        for st in self._stores:
            try:
                all_docs = st.get_all()
            except Exception as e:
                logger.warning(f"Pinned-chunks fetch: store.get_all() failed ({e!r})")
                continue
            for cid, doc, meta in zip(
                all_docs.ids, all_docs.documents, all_docs.metadatas,
            ):
                by_id.setdefault(cid, (doc, meta))
        out: list = []
        missing: list[str] = []
        for cid in ids:
            if cid not in by_id:
                missing.append(cid)
                continue
            doc, meta = by_id[cid]
            out.append(RetrievedChunk(
                chunk_id=cid,
                text=doc,
                metadata=dict(meta or {}),
                similarity_score=0.0,
                graph_node_id=cid,
            ))
        if missing:
            logger.warning(
                f"Pinned chunk IDs not found in store (vectorstore rebuilt?): "
                f"{missing[:5]}{'…' if len(missing) > 5 else ''}"
            )
        return out

    def query(
        self,
        query_text: str,
        verbose: bool = False,
        pinned_chunk_ids: list[str] | None = None,
        *,
        synthesizer=None,
    ) -> QueryResponse:
        """Run the full query pipeline.

        Args:
            query_text: Natural language query.
            verbose: If True, log intermediate results.
            pinned_chunk_ids: When provided, the pipeline skips Stages 2–4.7
                (resolver / graph scope / rewrite / RAG / threshold /
                grouping) and synthesizes from the chunks identified by
                these IDs. Used by the disambiguation UX (Step 3c): the
                user picked a group from a prior disambiguation response,
                and the system now answers using only that group's
                chunks. Stage 1 (analyzer) still runs so Stage 5 has a
                proper query_type. Unknown chunk IDs are dropped with a
                warning; if no IDs resolve, returns the not-found
                response.
            synthesizer: Keyword-only. Use this synthesizer for Stage 6
                instead of the one bound at construction. Lets a caller
                vary the LLM per query (e.g. a per-question reasoning
                level) while reusing the expensive cached pipeline —
                graph, embedder, vector store and BM25 index. None keeps
                the constructed synthesizer.

        Returns:
            QueryResponse with answer and citations.
        """
        # Stage 1: Query Analysis
        intent = self._analyzer.analyze(query_text)
        if verbose:
            logger.info(f"[Stage 1] Intent: {intent.to_dict()}")

        # Pinned-chunks short-circuit: skip Stages 2–4.7, fetch the
        # pinned chunks from the store, go straight to Stage 5/6.
        # Used by the disambiguation UX after the user picks a group.
        if pinned_chunk_ids:
            chunks = self._fetch_chunks_by_ids(list(pinned_chunk_ids))
            if not chunks:
                logger.info(
                    "Pinned chunk IDs did not resolve to any chunks "
                    "(vectorstore may have been rebuilt) — returning "
                    "not-found response"
                )
                return QueryResponse(
                    answer=_NOT_FOUND_ANSWER,
                    citations=[],
                    query_intent=intent,
                    candidate_count=0,
                    retrieved_count=0,
                )
            logger.info(
                f"Pinned-chunks path: synthesizing from "
                f"{len(chunks)}/{len(pinned_chunk_ids)} requested chunks"
            )
            context = self._context_builder.build(
                query_text, chunks, intent.query_type,
                max_context_chars=self._max_context_chars,
            )
            response = (synthesizer or self._synthesizer).synthesize(context, intent)
            response.candidate_count = 0
            response.retrieved_chunks = chunks
            response.assembled_context = context
            from core.src.query.citation_audit import audit_answer_citations
            available_req_ids = [
                c.metadata.get("req_id", "")
                for c in chunks
                if c.metadata.get("req_id")
            ]
            response.citation_audit = audit_answer_citations(
                response.answer, available_req_ids,
            )
            return response

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

        # Stage 3.5: Query rewriting (pre-retrieval expansion).
        # Per-query-type — single-doc lookups rely on entity-priority
        # graph scoping (D-039) so adding paraphrases is noise; concept-
        # shaped queries (cross-doc, standards-comparison, etc.) gain
        # recall from telecom-specific terminology the user query
        # didn't contain. The rewriter is no-op (MockQueryRewriter)
        # by default, so existing pipelines see unchanged behavior.
        retrieval_query = query_text
        if _TYPE_REWRITE_ENABLED.get(intent.query_type, False):
            rewrites = self._rewriter.rewrite(query_text)
            if rewrites:
                retrieval_query = expand_query(query_text, rewrites)
                if verbose:
                    logger.info(
                        f"[Stage 3.5] Rewrites ({len(rewrites)}): {rewrites}"
                    )
                else:
                    logger.info(
                        f"Query rewrite: {len(rewrites)} variants added"
                    )

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
        # Two-bucket resolver: every QueryType maps to broad (default 25)
        # or narrow (default 10). Resolution chain: cli > DB > env >
        # default. See env.config.resolve_top_k and the
        # nora-retrieval-parent-displacement strand.
        from core.src.env.config import resolve_top_k
        type_top_k = resolve_top_k(query_type=intent.query_type.value)
        # Apply user-configured cap (Config page) AFTER bucket widening
        # so an explicit "I want at most N" cap wins. None / 0 = no cap.
        if self._top_k_cap and self._top_k_cap > 0:
            type_top_k = min(type_top_k, self._top_k_cap)
        # BM25 weight: resolved through the unified chain so admins can
        # edit per-type values via the Config page without touching code.
        # Falls back to _TYPE_BM25_WEIGHT built-in defaults when no
        # override is set in DB / JSON. See D-041 (RRF fusion) + D-050
        # (3-tier resolver) + the Config page table editor.
        from core.src.env.config import resolve_bm25_weight
        bm25_weight = resolve_bm25_weight(query_type=intent.query_type.value)
        rerank = _TYPE_RERANK_ENABLED.get(intent.query_type, _DEFAULT_RERANK_ENABLED)
        # Per-query effective-knob log so admins can verify the
        # ConfigStore + resolver chain landed the values they expected.
        logger.info(
            "[Query knobs] type=%s top_k=%d (cap=%s) bm25_weight=%.2f rerank=%s "
            "rewrite=%s threshold=%s grouping=%s",
            intent.query_type.value,
            type_top_k,
            self._top_k_cap if self._top_k_cap else "none",
            bm25_weight,
            rerank,
            _TYPE_REWRITE_ENABLED.get(intent.query_type, False),
            _TYPE_MAX_DISTANCE.get(intent.query_type, self._max_distance_threshold),
            self._enable_grouping and intent.query_type not in _TYPE_DISABLE_GROUPING,
        )
        # D-DRAFT-11: route to the target cell(s). One cell → its retriever
        # directly (unchanged behavior). Multiple cells → retrieve per cell
        # WITHOUT per-cell rerank, merge, then rerank the pool once so the
        # cross-encoder compares across cells (merge-then-rerank). RRF scores
        # aren't cross-cell comparable, so without a real reranker the merged
        # pool is ordered by dense distance (same embedder ⇒ comparable).
        from core.src.query.reranker import MockReranker
        target_cells = self._select_cells(scoped)
        if len(target_cells) == 1:
            chunks = self._retrievers[target_cells[0]].retrieve(
                retrieval_query, candidates, scoped.scoped_mnos,
                top_k=type_top_k, bm25_weight=bm25_weight, rerank=rerank,
            )
        else:
            pool: list = []
            for ck in target_cells:
                pool.extend(self._retrievers[ck].retrieve(
                    retrieval_query, candidates, scoped.scoped_mnos,
                    top_k=type_top_k, bm25_weight=bm25_weight, rerank=False,
                ))
            if rerank and not isinstance(self._reranker, MockReranker):
                pool = self._reranker.rerank(retrieval_query, pool)
            else:
                pool.sort(key=lambda c: c.similarity_score)  # cosine distance: lower = better
            chunks = pool[:type_top_k]
            if verbose:
                logger.info(
                    f"[Stage 4] Multi-cell merge across {len(target_cells)} cells "
                    f"→ pool {len(pool)} → top {len(chunks)}"
                )
        if verbose:
            logger.info(
                f"[Stage 4] Retrieved: {len(chunks)} chunks "
                f"from {len(set(c.metadata.get('plan_id','') for c in chunks))} plans"
            )

        # Stage 4.5: Relevance threshold filter.
        # Per-query-type threshold takes precedence over the pipeline default.
        # Chunks with similarity_score (cosine distance) above the threshold
        # are too dissimilar to be useful; passing them to the LLM is the
        # primary source of hallucinated or confabulated answers.
        threshold = _TYPE_MAX_DISTANCE.get(
            intent.query_type, self._max_distance_threshold
        )
        if threshold is not None:
            before = len(chunks)
            chunks = [c for c in chunks if c.similarity_score <= threshold]
            dropped = before - len(chunks)
            if dropped:
                logger.info(
                    f"Threshold filter (max_distance={threshold}): "
                    f"dropped {dropped}/{before} chunks"
                )
            if not chunks:
                logger.info(
                    "All chunks below threshold — returning not-found response"
                )
                return QueryResponse(
                    answer=_NOT_FOUND_ANSWER,
                    citations=[],
                    query_intent=intent,
                    candidate_count=candidates.total,
                    retrieved_count=0,
                )

        # Stage 4.7: Hierarchy-based grouping (optional, opt-in via
        # `enable_grouping`). Clusters chunks by hierarchy_path
        # prefix; auto-commits to the top group when its score gap to
        # the next group exceeds the configured threshold; otherwise
        # short-circuits with a disambiguation response. See D-049
        # for rationale.
        # Per-type opt-out: SUMMARIZE (and any other intent in
        # _TYPE_DISABLE_GROUPING) bypasses Stage 4.7 because the
        # auto-commit-to-one-group behavior is semantically wrong for
        # breadth queries — the user wants every group merged into
        # one synthesis, not one group picked.
        type_grouping_active = (
            self._enable_grouping
            and intent.query_type not in _TYPE_DISABLE_GROUPING
        )
        if type_grouping_active and chunks:
            from core.src.env.config import resolve_gap_threshold
            from core.src.query.grouping import (
                gap_between_top_groups, group_chunks_by_hierarchy,
            )

            groups = group_chunks_by_hierarchy(chunks)
            if len(groups) > 1:
                gap_threshold = resolve_gap_threshold(
                    query_type=intent.query_type.value,
                )
                gap = gap_between_top_groups(groups)
                if gap >= gap_threshold:
                    # Clear winner — auto-commit to top group's chunks.
                    top_group = groups[0]
                    logger.info(
                        f"[Stage 4.7] Auto-commit to top group "
                        f"(prefix={top_group.common_prefix}, "
                        f"chunks={len(top_group.chunks)}/{len(chunks)}, "
                        f"gap={gap:.4f} >= threshold={gap_threshold})"
                    )
                    chunks = top_group.chunks
                else:
                    # Gap too narrow — return disambiguation, skip Stages 5/6.
                    logger.info(
                        f"[Stage 4.7] Disambiguation: {len(groups)} groups, "
                        f"gap={gap:.4f} < threshold={gap_threshold}"
                    )
                    return QueryResponse(
                        answer=_DISAMBIGUATION_ANSWER,
                        citations=[],
                        query_intent=intent,
                        candidate_count=candidates.total,
                        retrieved_count=sum(len(g.chunks) for g in groups),
                        retrieved_chunks=chunks,
                        disambiguation_required=True,
                        groups=groups,
                    )
            elif verbose:
                logger.info(
                    f"[Stage 4.7] Single group "
                    f"(prefix={groups[0].common_prefix if groups else []}); "
                    "no disambiguation needed"
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
        response = (synthesizer or self._synthesizer).synthesize(context, intent)
        response.candidate_count = candidates.total
        # Surface the retrieval set for the UI / Test page. This is
        # the post-rerank top-K returned by Stage 4 (RAG) — it's what
        # "All returned by RAG" means on the Test page.
        response.retrieved_chunks = chunks
        # Surface the exact prompt sent to the LLM so the Test page
        # can render a "Show LLM prompt" debug view.
        response.assembled_context = context
        # Surface Stage 3 output so the Test page can show what
        # graph scoping + taxonomy contributed (matched features +
        # candidate node sample). None when graph is bypassed.
        response.graph_candidates = candidates if not self._bypass_graph else None

        # Stage 6.5: Citation audit. Per-sentence breakdown of which
        # sentences cite a requirement and which don't, plus
        # detection of fabricated citations (req IDs in the answer
        # that weren't in the context). Cheap (regex over the answer);
        # always runs on the synthesis path. See citation_audit.py.
        from core.src.query.citation_audit import audit_answer_citations
        available_req_ids = [
            c.metadata.get("req_id", "")
            for c in chunks
            if c.metadata.get("req_id")
        ]
        response.citation_audit = audit_answer_citations(
            response.answer, available_req_ids,
        )

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


def build_stub_graph_from_store(store) -> nx.DiGraph:
    """Construct a minimal MNO/Release/Plan-only graph from the
    vector store's chunk metadata. Used when the full knowledge
    graph stage was skipped (`--rag-only` / `--skip-graph`) — the
    QueryPipeline still needs *something* graph-shaped for resolver
    construction, and `_bypass_graph=True` makes Stage 3 emit an
    empty CandidateSet so retrieval falls back to the metadata
    path.

    Built nodes:
      - mno:<MNO>
      - release:<MNO>:<RELEASE>
      - plan:<MNO>:<RELEASE>:<PLAN_ID>

    No Requirement / Standard / Feature nodes; no maps_to / depends_on
    edges. The resolver only walks the MNO → release edges to enumerate
    available scopes — that's enough.
    """
    g: nx.DiGraph = nx.DiGraph()
    seen_mno: set[str] = set()
    seen_release: set[str] = set()
    seen_plan: set[str] = set()
    try:
        all_ = store.get_all()
    except Exception as e:
        logger.warning(f"Stub graph: store.get_all() failed ({e!r}); empty graph")
        return g
    for meta in all_.metadatas:
        meta = meta or {}
        mno = (meta.get("mno") or "").strip()
        release = (meta.get("release") or "").strip()
        plan = (meta.get("plan_id") or "").strip()
        if mno and mno not in seen_mno:
            g.add_node(f"mno:{mno}", node_type="MNO", mno=mno, name=mno)
            seen_mno.add(mno)
        if mno and release:
            rid = f"release:{mno}:{release}"
            if rid not in seen_release:
                g.add_node(rid, node_type="Release", mno=mno, release=release)
                g.add_edge(f"mno:{mno}", rid, edge_type="has_release")
                seen_release.add(rid)
        if mno and release and plan:
            pid = f"plan:{mno}:{release}:{plan}"
            if pid not in seen_plan:
                g.add_node(
                    pid, node_type="Plan", plan_id=plan,
                    mno=mno, release=release,
                )
                g.add_edge(f"release:{mno}:{release}", pid, edge_type="contains_plan")
                seen_plan.add(pid)
    logger.info(
        f"Stub graph: {g.number_of_nodes()} nodes ({len(seen_mno)} MNOs, "
        f"{len(seen_release)} releases, {len(seen_plan)} plans), "
        f"{g.number_of_edges()} edges"
    )
    return g
