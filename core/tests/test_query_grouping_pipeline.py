"""Tests for Stage 4.7 (hierarchy grouping) pipeline integration.

Stage 4.7 sits between the threshold filter (D-047) and Stage 5
context assembly. When enabled and the gap between top groups is
below the resolved gap threshold, the pipeline returns a
disambiguation QueryResponse with `disambiguation_required=True`
and populated `groups`, skipping Stages 5/6.

Auto-commit (clear gap) keeps only the top group's chunks and
proceeds to synthesis as today.

Default behavior (`enable_grouping=False`) preserves pre-Step-3
pipeline output exactly.
"""

from __future__ import annotations

import math
from pathlib import Path

import networkx as nx
import pytest

from core.src.query.pipeline import (
    QueryPipeline,
    _DISAMBIGUATION_ANSWER,
    _NOT_FOUND_ANSWER,
)
from core.src.query.schema import RetrievedChunk
from core.src.vectorstore.store_base import QueryResult


# ── Mock providers (mirror test_query_threshold.py shape) ───────


class _FixedEmbedder:
    """Returns the zero vector for any input — store decides scoring."""
    def embed_query(self, text):
        return [0.0] * 8

    def embed(self, texts):
        return [[0.0] * 8] * len(texts)

    @property
    def dimension(self):
        return 8

    @property
    def model_name(self):
        return "fixed-zero"


class _ScriptedStore:
    """Returns a pre-built QueryResult regardless of the query embedding."""
    def __init__(self, result: QueryResult) -> None:
        self._result = result

    def query(self, query_embedding, n_results=10, where=None):
        return self._result

    @property
    def count(self):
        return len(self._result.ids)

    def reset(self):
        pass

    def get_all(self):
        return QueryResult(
            ids=self._result.ids,
            documents=self._result.documents,
            metadatas=self._result.metadatas,
            distances=[],
        )


def _make_result(chunks: list[tuple[str, list[str], float]]) -> QueryResult:
    """Build a QueryResult from (req_id, hierarchy_path, distance) tuples."""
    return QueryResult(
        ids=[f"req:{rid}" for rid, _, _ in chunks],
        documents=[f"text {rid}" for rid, _, _ in chunks],
        metadatas=[
            {
                "req_id": rid,
                "plan_id": "PLAN",
                "mno": "VZW",
                "release": "2026",
                "section_number": "1.0",
                "zone_type": "",
                "feature_ids": [],
                "hierarchy_path": list(path),
            }
            for rid, path, _ in chunks
        ],
        distances=[d for _, _, d in chunks],
    )


def _pipeline(chunks, enable_grouping: bool = True) -> QueryPipeline:
    g = nx.DiGraph()
    store = _ScriptedStore(_make_result(chunks))
    return QueryPipeline(
        graph=g,
        embedder=_FixedEmbedder(),
        store=store,
        enable_bm25=False,
        enable_grouping=enable_grouping,
    )


# ── Tests ────────────────────────────────────────────────────────


class TestGroupingDisabled:
    def test_default_does_not_group(self):
        """enable_grouping=False (default) → all chunks pass through."""
        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.21),  # narrow gap, would normally disambiguate
        ]
        p = _pipeline(chunks, enable_grouping=False)
        resp = p.query("test")
        assert resp.disambiguation_required is False
        assert resp.groups == []
        assert resp.retrieved_count == 2


class TestGroupingEnabled_SingleGroup:
    def test_chunks_share_path_no_disambiguation(self):
        """All chunks in one document → single group → pass through."""
        chunks = [
            ("R1", ["DOC", "Sec"], 0.2),
            ("R2", ["DOC", "Sec"], 0.3),
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        assert resp.disambiguation_required is False
        assert resp.retrieved_count == 2  # synthesis ran, all chunks used


class TestGroupingEnabled_AutoCommit:
    def test_clear_gap_auto_commits_to_top_group(self, monkeypatch):
        """Top group at 0.20, next group at 0.50 → gap 0.30 > 0.05 default → auto-commit."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A", "Sec"], 0.20),
            ("R2", ["DOC_A", "Sec"], 0.25),
            ("R3", ["DOC_B", "Sec"], 0.50),
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        assert resp.disambiguation_required is False
        # Top group has 2 chunks (DOC_A); DOC_B chunk dropped.
        assert resp.retrieved_count == 2

    def test_auto_commit_filters_to_top_group_chunks_only(self, monkeypatch):
        """The chunks passed to synthesis must be the top group's only."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R_A1", ["DOC_A"], 0.20),
            ("R_A2", ["DOC_A"], 0.25),
            ("R_B1", ["DOC_B"], 0.60),
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        # retrieved_chunks reflects the post-grouping set passed to synthesis.
        kept_req_ids = {c.metadata["req_id"] for c in resp.retrieved_chunks}
        assert "R_A1" in kept_req_ids
        assert "R_A2" in kept_req_ids
        assert "R_B1" not in kept_req_ids


class TestGroupingEnabled_Disambiguation:
    def test_narrow_gap_returns_disambiguation(self, monkeypatch):
        """Top group 0.20, next 0.22 → gap 0.02 < 0.05 → disambiguate."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.22),  # narrow gap
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        assert resp.disambiguation_required is True
        assert resp.answer == _DISAMBIGUATION_ANSWER
        assert len(resp.groups) == 2
        # Best group first.
        assert resp.groups[0].common_prefix == ["DOC_A"]
        assert resp.groups[1].common_prefix == ["DOC_B"]

    def test_disambiguation_skips_synthesis(self, monkeypatch):
        """When disambiguation fires, no LLM call is made — answer is the
        deterministic disambiguation message, citations empty."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.22),
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        assert resp.citations == []
        assert resp.answer == _DISAMBIGUATION_ANSWER

    def test_disambiguation_carries_intent_and_candidate_count(self, monkeypatch):
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.22),
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test")
        assert resp.query_intent is not None
        assert isinstance(resp.candidate_count, int)


class TestGapThresholdResolution:
    def test_env_var_overrides_default(self, monkeypatch):
        """Set NORA_RETRIEVAL_GAP_THRESHOLD=0.5 → previously-clear gap of 0.3 now disambiguates."""
        from core.src.env import config as env_cfg
        monkeypatch.setenv(env_cfg.GAP_THRESHOLD_ENV_VAR, "0.5")
        env_cfg._reset_retrieval_config_cache()
        try:
            chunks = [
                ("R1", ["DOC_A"], 0.20),
                ("R2", ["DOC_B"], 0.50),  # gap 0.30, but threshold now 0.5
            ]
            p = _pipeline(chunks, enable_grouping=True)
            resp = p.query("test")
            assert resp.disambiguation_required is True
        finally:
            env_cfg._reset_retrieval_config_cache()

    def test_strict_threshold_increases_disambiguation_rate(self, monkeypatch):
        """0.005 threshold → tiny gaps still trigger auto-commit."""
        from core.src.env import config as env_cfg
        monkeypatch.setenv(env_cfg.GAP_THRESHOLD_ENV_VAR, "0.005")
        env_cfg._reset_retrieval_config_cache()
        try:
            chunks = [
                ("R1", ["DOC_A"], 0.20),
                ("R2", ["DOC_B"], 0.21),  # gap 0.01 > 0.005 → auto-commit
            ]
            p = _pipeline(chunks, enable_grouping=True)
            resp = p.query("test")
            assert resp.disambiguation_required is False
        finally:
            env_cfg._reset_retrieval_config_cache()


class TestTopKCap:
    """Step: top_k_cap — hard upper limit applied AFTER per-type
    widening, so user-set caps win over breadth-query widening."""

    def test_no_cap_preserves_per_type_widening(self):
        """Without a cap, per-type widening lifts top_k for breadth
        queries (SUMMARIZE → 50 internally; here we just verify the
        retriever was asked for >= 25)."""
        # Make 60 chunks all in same path so retrieval can return many.
        triples = [(f"R{i}", ["DOC"], 0.2 + i * 0.001) for i in range(60)]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=False,
            top_k=10,  # floor
            top_k_cap=None,  # no cap
        )
        # SUMMARIZE-shaped query → broad bucket (default 25).
        # (Pre-strand behavior was 50 via the now-removed _TYPE_TOP_K
        # dict; the two-bucket design intentionally collapses to 25.)
        resp = p.query("Explain the X requirements")
        assert resp.retrieved_count == 25

    def test_cap_overrides_per_type_widening(self):
        """With cap=25, SUMMARIZE queries that would widen to 50
        retrieve only 25."""
        triples = [(f"R{i}", ["DOC"], 0.2 + i * 0.001) for i in range(60)]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=False,
            top_k=10, top_k_cap=25,
        )
        resp = p.query("Explain the X requirements")
        assert resp.retrieved_count == 25

    def test_cap_below_floor_still_caps(self):
        """If cap is below the floor (top_k=10, top_k_cap=5), the cap
        wins — explicit user constraint beats default floor."""
        triples = [(f"R{i}", ["DOC"], 0.2 + i * 0.001) for i in range(60)]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=False,
            top_k=10, top_k_cap=5,
        )
        resp = p.query("Lookup query")
        assert resp.retrieved_count == 5

    def test_cap_zero_treated_as_no_cap(self):
        """Cap of 0 is treated as 'unset' — sentinel for the Config
        UI's empty-input → 0 coercion."""
        triples = [(f"R{i}", ["DOC"], 0.2 + i * 0.001) for i in range(60)]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=False,
            top_k=10, top_k_cap=0,
        )
        resp = p.query("Explain the X requirements")
        # No cap → SUMMARIZE returns broad bucket default (25).
        assert resp.retrieved_count == 25


class TestPinnedChunksPath:
    """Step 3c — pinned_chunk_ids skips Stages 2-4.7 and synthesizes
    only from the named chunks. Used to resolve disambiguation
    after the user picks a group."""

    def test_pinned_chunks_skip_retrieval(self):
        """When pinned_chunk_ids is set, retrieval is bypassed —
        the chunks come from the store directly."""
        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.50),
        ]
        p = _pipeline(chunks, enable_grouping=False)
        # Pin only R2 (which would NOT have been the top dense match).
        resp = p.query("test", pinned_chunk_ids=["req:R2"])
        kept = {c.metadata["req_id"] for c in resp.retrieved_chunks}
        assert kept == {"R2"}
        assert resp.disambiguation_required is False

    def test_pinned_chunks_skip_grouping_short_circuit(self):
        """Even when grouping is enabled, pinned-chunks goes straight
        to synthesis — no disambiguation can fire."""
        chunks = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.21),  # would normally disambiguate
        ]
        p = _pipeline(chunks, enable_grouping=True)
        resp = p.query("test", pinned_chunk_ids=["req:R1"])
        assert resp.disambiguation_required is False
        kept = {c.metadata["req_id"] for c in resp.retrieved_chunks}
        assert kept == {"R1"}

    def test_pinned_unknown_ids_returns_not_found(self):
        """If none of the pinned IDs resolve in the store, we get the
        not-found response (vectorstore was rebuilt or IDs are bogus)."""
        chunks = [("R1", ["DOC"], 0.2)]
        p = _pipeline(chunks, enable_grouping=False)
        resp = p.query("test", pinned_chunk_ids=["req:DOES_NOT_EXIST"])
        assert resp.answer == _NOT_FOUND_ANSWER
        assert resp.retrieved_count == 0

    def test_pinned_partial_match_drops_unknowns(self):
        """Mix of known and unknown IDs: known are kept, unknowns
        dropped with a warning. Synthesis runs on what resolved."""
        chunks = [
            ("R1", ["DOC"], 0.2),
            ("R2", ["DOC"], 0.3),
        ]
        p = _pipeline(chunks, enable_grouping=False)
        resp = p.query("test", pinned_chunk_ids=["req:R1", "req:GHOST"])
        assert resp.disambiguation_required is False
        kept = {c.metadata["req_id"] for c in resp.retrieved_chunks}
        assert kept == {"R1"}

    def test_pinned_chunks_score_set_to_zero(self):
        """Pinned chunks get similarity_score=0.0 — user explicitly
        picked them, no ranking required."""
        chunks = [("R1", ["DOC"], 0.45)]
        p = _pipeline(chunks, enable_grouping=False)
        resp = p.query("test", pinned_chunk_ids=["req:R1"])
        assert resp.retrieved_chunks[0].similarity_score == 0.0


class TestInteractionWithThreshold:
    def test_threshold_filter_runs_before_grouping(self, monkeypatch):
        """Chunks above max_distance_threshold are dropped first — grouping
        operates on survivors only. With threshold 0.3 and a chunk at 0.4,
        only chunks <= 0.3 form groups."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A"], 0.20),  # passes threshold
            ("R2", ["DOC_B"], 0.40),  # filtered by threshold
        ]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(chunks))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False,
            enable_grouping=True,
            max_distance_threshold=0.3,
        )
        resp = p.query("test")
        # Only DOC_A survived threshold → single group → no disambiguation.
        assert resp.disambiguation_required is False
        assert resp.retrieved_count == 1

    def test_all_chunks_filtered_returns_not_found_not_disambiguation(self, monkeypatch):
        """When threshold drops every chunk, _NOT_FOUND_ANSWER fires before
        Stage 4.7 even runs."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        chunks = [
            ("R1", ["DOC_A"], 0.80),
            ("R2", ["DOC_B"], 0.85),
        ]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(chunks))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False,
            enable_grouping=True,
            max_distance_threshold=0.5,
        )
        resp = p.query("test")
        assert resp.answer == _NOT_FOUND_ANSWER
        assert resp.disambiguation_required is False
