"""Tests for the relevance threshold filter in QueryPipeline (Stage 4.5).

The pipeline accepts a `max_distance_threshold` parameter. After Stage 4
(RAG), chunks with similarity_score (cosine distance) above the threshold
are discarded. When every chunk is discarded, the pipeline returns a
deterministic "not found" answer instead of synthesizing from weak fragments.

similarity_score stores the raw ChromaDB cosine distance (lower = more
similar). The threshold is therefore an *upper* bound: keep chunks where
score <= threshold.
"""

from __future__ import annotations

import math

import networkx as nx
import pytest

from core.src.query.pipeline import QueryPipeline, _NOT_FOUND_ANSWER
from core.src.query.schema import RetrievedChunk, QueryResponse
from core.src.vectorstore.store_base import QueryResult


# ── Minimal mock providers ───────────────────────────────────────


class _FixedEmbedder:
    """Always returns the zero vector — cosine distance is then 1.0."""

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8] * len(texts)

    @property
    def dimension(self) -> int:
        return 8

    @property
    def model_name(self) -> str:
        return "fixed-zero"


class _ScriptedStore:
    """Returns a pre-built QueryResult regardless of the query."""

    def __init__(self, result: QueryResult) -> None:
        self._result = result

    def query(self, query_embedding, n_results=10, where=None):
        return self._result

    @property
    def count(self) -> int:
        return len(self._result.ids)

    def reset(self) -> None:
        pass

    def get_all(self) -> QueryResult:
        return QueryResult(
            ids=self._result.ids,
            documents=self._result.documents,
            metadatas=self._result.metadatas,
            distances=[],
        )


def _make_result(scores: list[float]) -> QueryResult:
    """Build a QueryResult with one chunk per score value."""
    n = len(scores)
    return QueryResult(
        ids=[f"req:REQ_{i}" for i in range(n)],
        documents=[f"text {i}" for i in range(n)],
        metadatas=[
            {
                "req_id": f"REQ_{i}",
                "plan_id": "PLAN",
                "mno": "VZW",
                "release": "2026",
                "section_number": "1.0",
                "zone_type": "",
                "feature_ids": [],
                "hierarchy_path": [],
            }
            for i in range(n)
        ],
        distances=scores,
    )


def _pipeline(scores: list[float], threshold: float | None) -> QueryPipeline:
    """Construct a minimal pipeline with scripted retrieval results."""
    g = nx.DiGraph()
    store = _ScriptedStore(_make_result(scores))
    return QueryPipeline(
        graph=g,
        embedder=_FixedEmbedder(),
        store=store,
        max_distance_threshold=threshold,
        enable_bm25=False,  # BM25 irrelevant for threshold tests
    )


# ── Tests ────────────────────────────────────────────────────────


class TestThresholdDisabled:
    def test_none_threshold_returns_all_chunks(self):
        p = _pipeline([0.1, 0.5, 0.9], threshold=None)
        resp = p.query("test query")
        assert resp.retrieved_count == 3
        assert _NOT_FOUND_ANSWER not in resp.answer

    def test_chunks_with_high_distance_pass_through_when_no_threshold(self):
        p = _pipeline([1.5, 1.8, 1.9], threshold=None)
        resp = p.query("test query")
        assert resp.retrieved_count == 3


class TestThresholdFiltersChunks:
    def test_all_above_threshold_returns_not_found(self):
        p = _pipeline([0.8, 0.9, 1.0], threshold=0.5)
        resp = p.query("test query")
        assert resp.answer == _NOT_FOUND_ANSWER
        assert resp.retrieved_count == 0
        assert resp.citations == []

    def test_all_below_threshold_passes_all(self):
        p = _pipeline([0.1, 0.2, 0.3], threshold=0.5)
        resp = p.query("test query")
        assert resp.retrieved_count == 3
        assert resp.answer != _NOT_FOUND_ANSWER

    def test_mixed_scores_keeps_only_passing_chunks(self):
        # scores: two pass (0.2, 0.4), one fails (0.7)
        p = _pipeline([0.2, 0.7, 0.4], threshold=0.5)
        resp = p.query("test query")
        assert resp.retrieved_count == 2
        assert resp.answer != _NOT_FOUND_ANSWER

    def test_exactly_at_threshold_is_kept(self):
        p = _pipeline([0.5], threshold=0.5)
        resp = p.query("test query")
        assert resp.retrieved_count == 1
        assert resp.answer != _NOT_FOUND_ANSWER

    def test_just_above_threshold_is_dropped(self):
        p = _pipeline([0.51], threshold=0.5)
        resp = p.query("test query")
        assert resp.answer == _NOT_FOUND_ANSWER
        assert resp.retrieved_count == 0


class TestNotFoundResponse:
    def test_not_found_carries_intent(self):
        p = _pipeline([0.9], threshold=0.3)
        resp = p.query("test query")
        assert resp.query_intent is not None

    def test_not_found_carries_candidate_count(self):
        """candidate_count is 0 (empty graph → no graph candidates); still set."""
        p = _pipeline([0.9], threshold=0.3)
        resp = p.query("test query")
        assert isinstance(resp.candidate_count, int)

    def test_not_found_message_is_non_empty(self):
        assert _NOT_FOUND_ANSWER
        assert len(_NOT_FOUND_ANSWER) > 20

    def test_not_found_has_no_citations(self):
        p = _pipeline([0.8, 0.9], threshold=0.1)
        resp = p.query("test query")
        assert resp.citations == []


class TestThresholdStrictness:
    def test_strict_threshold_drops_moderate_chunks(self):
        p = _pipeline([0.1, 0.3, 0.5, 0.7], threshold=0.25)
        resp = p.query("test query")
        # Only the 0.1 chunk passes
        assert resp.retrieved_count == 1

    def test_lenient_threshold_keeps_most_chunks(self):
        p = _pipeline([0.1, 0.3, 0.5, 0.7], threshold=0.8)
        resp = p.query("test query")
        assert resp.retrieved_count == 4
