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
    """Types of queries the pipeline can handle."""
    SINGLE_DOC = "single_doc"
    CROSS_DOC = "cross_doc"
    CROSS_MNO_COMPARISON = "cross_mno_comparison"
    RELEASE_DIFF = "release_diff"
    STANDARDS_COMPARISON = "standards_comparison"
    TRACEABILITY = "traceability"
    FEATURE_LEVEL = "feature_level"
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


# ── Stage 6 output ──────────────────────────────────────────────


@dataclass
class Citation:
    """A citation to a specific requirement or standard."""
    req_id: str = ""
    plan_id: str = ""
    section_number: str = ""
    spec: str = ""
    spec_section: str = ""


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

    def to_dict(self) -> dict[str, Any]:
        d = {
            "answer": self.answer,
            "citations": [asdict(c) for c in self.citations],
            "candidate_count": self.candidate_count,
            "retrieved_count": self.retrieved_count,
            "context_tokens_approx": self.context_tokens_approx,
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
