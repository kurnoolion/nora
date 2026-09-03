"""Query pipeline data models (TDD 7.1-7.6).

Defines the data structures passed between pipeline stages:
  QueryIntent   — output of query analysis (Stage 1)
  ScopedQuery   — output of MNO/release resolution (Stage 2)
  CandidateSet  — output of graph scoping (Stage 3)
  RetrievedChunk — output of targeted RAG (Stage 4)
  AssembledContext — output of context assembly (Stage 5)
  QueryResponse — final output (Stage 6)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class QueryType(str, Enum):
    """Types of queries the pipeline can handle.

    Step 4 added intent-shaped types FACT and SUMMARIZE alongside the
    existing shape-/scope-based types. The analyzer classifies based on
    query phrasing; the pipeline then routes per-type knobs (top_k,
    BM25 weight, rerank, threshold, grouping disable, system prompt).
    """
    SINGLE_DOC = "single_doc"
    CROSS_DOC = "cross_doc"
    CROSS_MNO_COMPARISON = "cross_mno_comparison"
    RELEASE_DIFF = "release_diff"
    STANDARDS_COMPARISON = "standards_comparison"
    TRACEABILITY = "traceability"
    FEATURE_LEVEL = "feature_level"
    SUMMARIZE = "summarize"
    """Survey/summarize intent — "Explain X requirements", "Summarize X",
    "Describe X", "Overview of X". Wide top_k, grouping disabled
    (don't pick one group — synthesize across all retrieved), prompt
    asks for TL;DR + per-section breakdown. The default-off-by-corpus
    threshold filter stays disabled here too (we want breadth)."""
    FACT = "fact"
    """Fact-lookup intent — "What is the value of X", "How many", "How
    long", "Default value of". Tight top_k, strict max_distance_threshold
    (only confident matches), rerank ON (precision matters). Synthesis
    prompt asks for short fact-centric answer with per-sentence
    attribution and explicit contradiction surfacing when sources
    disagree."""
    GENERAL = "general"


class DocTypeScope(str, Enum):
    """Which document types to include in retrieval."""
    REQUIREMENTS = "requirements"
    TEST_CASES = "test_cases"
    BOTH = "both"


# ── Stage 1 output ──────────────────────────────────────────────


@dataclass
class QueryIntent:
    """Structured intent extracted from a natural language query.

    Output of Stage 1 (Query Analysis).
    """
    raw_query: str
    entities: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    mnos: list[str] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)
    query_type: QueryType = QueryType.GENERAL
    doc_type_scope: DocTypeScope = DocTypeScope.REQUIREMENTS
    standards_refs: list[str] = field(default_factory=list)
    likely_features: list[str] = field(default_factory=list)
    plan_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["query_type"] = self.query_type.value
        d["doc_type_scope"] = self.doc_type_scope.value
        return d


# ── Stage 2 output ──────────────────────────────────────────────


@dataclass
class MNOScope:
    """A resolved MNO + release pair."""
    mno: str
    release: str


@dataclass
class ScopedQuery:
    """Query with resolved MNO/release scope.

    Output of Stage 2 (MNO/Release Resolution).
    """
    intent: QueryIntent
    scoped_mnos: list[MNOScope] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "scoped_mnos": [asdict(s) for s in self.scoped_mnos],
        }


# ── Stage 3 output ──────────────────────────────────────────────


@dataclass
class CandidateNode:
    """A candidate node from graph scoping."""
    node_id: str
    node_type: str
    score: float = 1.0
    source: str = ""  # how it was found: "entity", "feature", "traversal"
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSet:
    """Set of candidate nodes from graph scoping.

    Output of Stage 3 (Graph Scoping).
    """
    requirement_nodes: list[CandidateNode] = field(default_factory=list)
    standards_nodes: list[CandidateNode] = field(default_factory=list)
    feature_nodes: list[CandidateNode] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.requirement_nodes)
            + len(self.standards_nodes)
            + len(self.feature_nodes)
        )

    def requirement_ids(self) -> list[str]:
        """Return req_id values (not graph node IDs) for vector store filtering."""
        return [
            n.attributes.get("req_id", "")
            for n in self.requirement_nodes
            if n.attributes.get("req_id")
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "requirement_nodes": len(self.requirement_nodes),
            "standards_nodes": len(self.standards_nodes),
            "feature_nodes": len(self.feature_nodes),
        }


# ── Stage 4 output ──────────────────────────────────────────────


@dataclass
class RetrievedChunk:
    """A chunk retrieved and ranked by vector similarity."""
    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    similarity_score: float = 0.0
    graph_node_id: str = ""

    # Per-chunk retrieval provenance for the Test page / debugging.
    # Keys are populated by ``RAGRetriever._fuse`` (when hybrid) and
    # by reranker / glossary-pin steps. None / missing keys mean "the
    # corresponding retriever didn't return this chunk in its top-K".
    # Schema (all optional):
    #   dense_rank: int       — 0-based rank in dense top-K
    #   bm25_rank: int        — 0-based rank in BM25 top-K
    #   rrf_score: float      — RRF score after fusion
    #   reranker_rank_in: int — rank before cross-encoder rerank
    #   reranker_rank_out: int — rank after cross-encoder rerank
    #   source: str           — "glossary_pin" when prepended by D-043
    retrieval_meta: dict[str, Any] = field(default_factory=dict)


# ── Stage 5 output ──────────────────────────────────────────────


@dataclass
class StandardsContext:
    """Standards text associated with a requirement."""
    spec: str = ""
    section: str = ""
    release_num: int = 0
    title: str = ""
    text: str = ""


@dataclass
class ChunkContext:
    """A chunk with full context for LLM prompt assembly."""
    chunk: RetrievedChunk
    hierarchy_path: list[str] = field(default_factory=list)
    parent_text: str = ""
    standards: list[StandardsContext] = field(default_factory=list)
    related_chunk_ids: list[str] = field(default_factory=list)


@dataclass
class AssembledContext:
    """Assembled LLM prompt context.

    Output of Stage 5 (Context Assembly).
    """
    system_prompt: str = ""
    context_text: str = ""
    chunks: list[ChunkContext] = field(default_factory=list)
    query_type: QueryType = QueryType.GENERAL


# ── Stage 4.7 (grouping) output ─────────────────────────────────


@dataclass
class ChunkGroup:
    """A cluster of retrieved chunks sharing a hierarchy-path prefix.

    Output of Stage 4.7 (hierarchy-based grouping). The pipeline either
    auto-commits to the top-scoring group (passing its chunks to Stage 5
    synthesis) or, when the gap between top groups is below the
    configured threshold, returns a disambiguation `QueryResponse` with
    multiple groups for the UI to surface as user-facing choices.
    """
    common_prefix: list[str]
    """Longest hierarchy-path prefix shared by every chunk in the group.
    First element is the document root (per D-046)."""

    chunks: list[RetrievedChunk]
    """Chunks belonging to this group, in retrieval-rank order."""

    score: float = 0.0
    """Group score — minimum cosine distance across `chunks`. Lower is
    better (closer to query). Min rather than avg so a single strong
    chunk is recognized as a strong group; weak siblings don't drag it
    down."""

    representative_titles: list[str] = field(default_factory=list)
    """Up to N short title strings (req titles or section names) for
    UX display. Order mirrors `chunks`. Computed at group construction
    so the UI doesn't have to reach back into chunk text."""


# ── Stage 6.5 (citation audit) output ───────────────────────────


@dataclass
class SentenceAudit:
    """One sentence's audit result. Output of post-synthesis Stage 6.5.

    A "factual" sentence is anything that's not markdown header / TL;DR
    label / blank — i.e. a sentence the user reads as a substantive
    claim. Only factual sentences contribute to the cited-percentage
    metric. Meta sentences are listed for completeness but don't
    affect the score.
    """
    text: str
    has_citation: bool
    citations_found: list[str] = field(default_factory=list)
    """Citation tokens recognized in the sentence — req_id strings
    (e.g. "VZ_REQ_LTEDATARETRY_7748") and 3GPP refs (e.g.
    "3GPP TS 24.301 Section 5.5.1.2.6")."""

    fabricated_citations: list[str] = field(default_factory=list)
    """Citations that look well-formed but DO NOT appear in the
    retrieved context. The worst-case error class — looks
    authoritative, isn't real."""

    is_meta: bool = False
    """True for markdown headers, TL;DR labels, blank lines — sentences
    that don't make factual claims and shouldn't count against the
    cited-percentage metric."""


@dataclass
class CitationAudit:
    """Full audit of a synthesized answer. Output of Stage 6.5."""
    sentences: list[SentenceAudit] = field(default_factory=list)
    cited_sentence_count: int = 0
    factual_sentence_count: int = 0
    """Total minus meta sentences. Denominator for cited-percentage."""

    fabricated_count: int = 0
    available_req_ids: list[str] = field(default_factory=list)
    """The req_ids that were actually in the LLM's context. Used by
    the audit to distinguish real citations from fabricated ones."""

    @property
    def cited_percent(self) -> float:
        if self.factual_sentence_count == 0:
            return 100.0
        return 100.0 * self.cited_sentence_count / self.factual_sentence_count

    @property
    def uncited_sentences(self) -> list[SentenceAudit]:
        return [s for s in self.sentences if not s.is_meta and not s.has_citation]


# ── Stage 6 output ──────────────────────────────────────────────


@dataclass
class Citation:
    """A citation to a specific requirement or standard.

    `llm_cited=True` means the LLM mentioned this in its answer text;
    `False` means the chunk was in the LLM's context (returned by RAG)
    but not explicitly cited. The Test page surfaces both views.
    """
    req_id: str = ""
    plan_id: str = ""
    section_number: str = ""
    spec: str = ""
    spec_section: str = ""
    llm_cited: bool = False


# ── Stage timing vocabulary ─────────────────────────────────────

#: Canonical per-stage timing slugs, in pipeline execution order.
#:
#: Each entry is ``(slug, display_label, band)``. `slug` is the key
#: written into `QueryResponse.timings_ms`; `display_label` is what the
#: UI shows; `band` is the coarse phase the stage belongs to.
#:
#: The bands exist so the NORA lane (ten stages) and the SIRA lane
#: (expand / search / rerank / synth) can be read side by side — the
#: stage names are not comparable across lanes, but the bands are.
STAGE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("analyze",      "Analyze",        "prep"),
    ("resolve_scope", "Resolve scope", "prep"),
    ("graph_scope",  "Graph scope",    "prep"),
    ("rewrite",      "Rewrite",        "prep"),
    ("fetch_pinned", "Fetch pinned",   "retrieval"),
    ("retrieve",     "Retrieve (RAG)", "retrieval"),
    ("threshold",    "Threshold",      "retrieval"),
    ("group",        "Group",          "retrieval"),
    ("assemble",     "Assemble",       "synthesis"),
    ("synthesize",   "Synthesize",     "synthesis"),
    ("audit",        "Citation audit", "post"),
)

#: Coarse bands in render order. `unaccounted` is not a stage — it is
#: the difference between the measured total and the sum of the parts,
#: and it is rendered explicitly so the timeline never over-claims
#: coverage it does not have.
BAND_ORDER: tuple[str, ...] = ("prep", "retrieval", "synthesis", "post", "unaccounted")


@dataclass
class QueryResponse:
    """Final pipeline output.

    Output of Stage 6 (LLM Synthesis).
    """
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    query_intent: QueryIntent | None = None
    candidate_count: int = 0
    retrieved_count: int = 0
    context_tokens_approx: int = 0
    # Full RAG retrieval — populated by QueryPipeline.query so the
    # Test page can render "all returned by RAG" alongside the
    # subset cited by the LLM. Off the LLM hot-path.
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)

    # Stage 4.7 disambiguation. Set when grouping is enabled and the
    # gap between the top two groups is below `gap_threshold` — the
    # pipeline returns groups for the user to pick from instead of
    # synthesizing one collapsed answer. UI surfaces them as cards;
    # CLI consumers see `answer = _DISAMBIGUATION_ANSWER`.
    disambiguation_required: bool = False
    groups: list[ChunkGroup] = field(default_factory=list)

    # Stage 5 output captured for debugging — the exact system prompt
    # and context text passed to the LLM for synthesis. Populated by
    # QueryPipeline.query() so the Test page can render a "Show LLM
    # prompt" view; debugging-only, not needed by callers that just
    # want the answer.
    assembled_context: AssembledContext | None = None

    # Stage 6.5 citation audit — per-sentence breakdown of which
    # sentences cite a requirement and which don't. Also detects
    # "fabricated" citations (req IDs in the answer that weren't in
    # the LLM's context). Populated by QueryPipeline.query() on the
    # normal synthesis path; None on disambiguation / not-found
    # paths (no real answer to audit).
    citation_audit: CitationAudit | None = None

    # Stage 3 output captured for Test-page visibility. The Test page
    # renders a "Graph & Taxonomy" panel showing matched features
    # (from intent / taxonomy) + graph-scoping candidate counts and a
    # sample of top req_ids / feature names. None when graph is
    # bypassed (RAG-only mode).
    graph_candidates: "CandidateSet | None" = None

    # Per-stage wall-clock timings in milliseconds, populated by
    # QueryPipeline.query() on every return path. Keys are the stage
    # slugs in `STAGE_ORDER`, plus `total_ms` for the whole call.
    #
    # A stage that did not run is ABSENT, not zero — Stages 3, 3.5,
    # 4.5 and 4.7 are all conditional, and a zero would render a
    # bypassed stage as instantaneous rather than skipped. Consumers
    # must treat a missing key as "did not run".
    #
    # The per-stage values do not sum to `total_ms`; the remainder is
    # un-instrumented overhead. Use `unaccounted_ms` to render it
    # honestly rather than letting the segments silently under-report.
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def unaccounted_ms(self) -> int:
        """`total_ms` minus the sum of the per-stage timings.

        Never negative — a clock that disagrees with itself clamps to 0
        rather than rendering a negative segment.
        """
        total = self.timings_ms.get("total_ms", 0)
        parts = sum(v for k, v in self.timings_ms.items() if k != "total_ms")
        return max(0, total - parts)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "answer": self.answer,
            "citations": [asdict(c) for c in self.citations],
            "candidate_count": self.candidate_count,
            "retrieved_count": self.retrieved_count,
            "context_tokens_approx": self.context_tokens_approx,
            "timings_ms": dict(self.timings_ms),
        }
        if self.query_intent:
            d["query_intent"] = self.query_intent.to_dict()
        return d

    def save_json(self, path) -> None:
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
