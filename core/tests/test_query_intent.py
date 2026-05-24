"""Tests for Step 4 — FACT and SUMMARIZE intent classification + routing.

Covers:
- Analyzer triggers route fact-shaped and summary-shaped queries to the
  new types instead of falling through to SINGLE_DOC / GENERAL.
- resolve_top_k routes FACT/SUMMARIZE to narrow/broad buckets;
  _TYPE_BM25_WEIGHT, _TYPE_RERANK_ENABLED, _TYPE_REWRITE_ENABLED,
  _TYPE_MAX_DISTANCE entries are populated.
- _TYPE_DISABLE_GROUPING contains SUMMARIZE so Stage 4.7 bypasses.
- System prompts cover both intents.
"""

from __future__ import annotations

import networkx as nx

from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.context_builder import _SYSTEM_PROMPTS
from core.src.env.config import resolve_top_k
from core.src.query.pipeline import (
    QueryPipeline,
    _DISAMBIGUATION_ANSWER,
    _TYPE_BM25_WEIGHT,
    _TYPE_DISABLE_GROUPING,
    _TYPE_MAX_DISTANCE,
    _TYPE_RERANK_ENABLED,
    _TYPE_REWRITE_ENABLED,
)
from core.src.query.schema import QueryType
from core.src.vectorstore.store_base import QueryResult


# ── Analyzer classification ────────────────────────────────────


class TestSummarizeClassification:
    """Phrasing → SUMMARIZE intent."""
    a = MockQueryAnalyzer()

    def test_explain_x_classifies_as_summarize(self):
        intent = self.a.analyze("Explain authentication requirements")
        assert intent.query_type == QueryType.SUMMARIZE

    def test_summarize_x_classifies(self):
        intent = self.a.analyze("Summarize the OTADM auth flow")
        assert intent.query_type == QueryType.SUMMARIZE

    def test_describe_x_classifies(self):
        intent = self.a.analyze("Describe how attach reject is handled")
        assert intent.query_type == QueryType.SUMMARIZE

    def test_overview_classifies(self):
        intent = self.a.analyze("Give me an overview of LTE data retry")
        assert intent.query_type == QueryType.SUMMARIZE

    def test_tell_me_about_classifies(self):
        intent = self.a.analyze("Tell me about T3402 timer behavior")
        assert intent.query_type == QueryType.SUMMARIZE


class TestFactClassification:
    """Phrasing → FACT intent."""
    a = MockQueryAnalyzer()

    def test_value_of_classifies(self):
        intent = self.a.analyze("What is the value of T3402?")
        assert intent.query_type == QueryType.FACT

    def test_default_value_classifies(self):
        intent = self.a.analyze("What is the default value of T3411?")
        assert intent.query_type == QueryType.FACT

    def test_how_many_classifies(self):
        intent = self.a.analyze("How many attach attempts are allowed?")
        assert intent.query_type == QueryType.FACT

    def test_how_long_classifies(self):
        intent = self.a.analyze("How long is the T3402 timer?")
        assert intent.query_type == QueryType.FACT

    def test_maximum_value_classifies(self):
        intent = self.a.analyze("What is the maximum value of MAX_RETRY?")
        assert intent.query_type == QueryType.FACT

    def test_threshold_classifies(self):
        intent = self.a.analyze("What is the threshold for retry?")
        assert intent.query_type == QueryType.FACT


class TestClassificationPriority:
    """Verify classification ordering when phrases overlap."""
    a = MockQueryAnalyzer()

    def test_fact_beats_summarize_when_both_present(self):
        """'Explain the value of T3402' has both 'explain' and 'value of'.
        FACT wins because it's checked first (specific-value queries
        warrant precision-focused routing even when phrased verbosely)."""
        intent = self.a.analyze("Explain the value of T3402 timer")
        assert intent.query_type == QueryType.FACT

    def test_what_is_x_alone_does_not_classify_as_fact(self):
        """Bare 'what is X' is definitional, handled by D-043 acronym
        pin or falls through. Should NOT be FACT (no quantitative
        trigger)."""
        intent = self.a.analyze("What is OTADM?")
        assert intent.query_type != QueryType.FACT

    def test_explain_in_isolation_routes_to_summarize_even_with_plan_alias(self):
        """'Explain X' should beat the SINGLE_DOC fallback that would
        otherwise route any query naming a known plan to SINGLE_DOC."""
        intent = self.a.analyze("Explain LTE OTA DM authentication requirements")
        assert intent.query_type == QueryType.SUMMARIZE


# ── Per-type knobs populated ───────────────────────────────────


class TestPerTypeKnobs:
    def test_summarize_top_k_is_wide(self):
        # SUMMARIZE → broad bucket (default 25). Wider than the narrow
        # default (10) used by FACT/SINGLE_DOC/GENERAL.
        assert resolve_top_k(QueryType.SUMMARIZE.value) >= 25

    def test_fact_max_distance_is_strict(self):
        # Stricter than the conservative pipeline default (~0.5)
        assert _TYPE_MAX_DISTANCE[QueryType.FACT] <= 0.5

    def test_summarize_max_distance_is_lenient(self):
        # Looser than fact — wants breadth
        assert _TYPE_MAX_DISTANCE[QueryType.SUMMARIZE] > _TYPE_MAX_DISTANCE[QueryType.FACT]

    def test_fact_rerank_on(self):
        assert _TYPE_RERANK_ENABLED.get(QueryType.FACT) is True

    def test_summarize_rerank_off(self):
        # SUMMARIZE explicitly excluded from rerank (cost vs benefit)
        assert _TYPE_RERANK_ENABLED.get(QueryType.SUMMARIZE) is not True

    def test_fact_rewrite_off(self):
        # Fact queries should not be paraphrased
        assert _TYPE_REWRITE_ENABLED.get(QueryType.FACT) is not True

    def test_summarize_rewrite_on(self):
        assert _TYPE_REWRITE_ENABLED.get(QueryType.SUMMARIZE) is True

    def test_fact_bm25_weight_set(self):
        assert _TYPE_BM25_WEIGHT.get(QueryType.FACT, 0.0) > 0.0

    def test_summarize_grouping_disabled(self):
        assert QueryType.SUMMARIZE in _TYPE_DISABLE_GROUPING

    def test_fact_grouping_not_disabled(self):
        # FACT can use grouping — one fact = one group is fine
        assert QueryType.FACT not in _TYPE_DISABLE_GROUPING


# ── System prompts populated ───────────────────────────────────


class TestSystemPrompts:
    def test_summarize_prompt_exists(self):
        prompt = _SYSTEM_PROMPTS[QueryType.SUMMARIZE]
        assert prompt
        assert "TL;DR" in prompt or "tl;dr" in prompt.lower()

    def test_fact_prompt_exists(self):
        prompt = _SYSTEM_PROMPTS[QueryType.FACT]
        assert prompt
        assert "contradict" in prompt.lower() or "contradiction" in prompt.lower()

    def test_fact_prompt_demands_per_sentence_attribution(self):
        prompt = _SYSTEM_PROMPTS[QueryType.FACT]
        assert "per-sentence" in prompt.lower() or "attribution" in prompt.lower()


# ── Stage 4.7 grouping bypass for SUMMARIZE ────────────────────


class _FixedEmbedder:
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
    def __init__(self, result):
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


def _make_result(triples):
    return QueryResult(
        ids=[f"req:{rid}" for rid, _, _ in triples],
        documents=[f"text {rid}" for rid, _, _ in triples],
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
            for rid, path, _ in triples
        ],
        distances=[d for _, _, d in triples],
    )


class TestSummarizeBypassesGrouping:
    """When SUMMARIZE intent + grouping enabled + narrow gap, the
    pipeline must NOT short-circuit with disambiguation — SUMMARIZE
    is in _TYPE_DISABLE_GROUPING."""

    def test_summarize_query_skips_grouping_even_with_narrow_gap(self, monkeypatch):
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        # Two groups with narrow gap — would normally trigger disambiguation
        triples = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.21),
        ]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=True,
        )
        # SUMMARIZE-shaped query — analyzer routes to QueryType.SUMMARIZE
        resp = p.query("Explain everything about feature X")
        # No disambiguation — grouping was bypassed for SUMMARIZE
        assert resp.disambiguation_required is False
        assert resp.answer != _DISAMBIGUATION_ANSWER
        # All chunks reach synthesis (synthesizer mock summarizes them)
        assert resp.retrieved_count == 2

    def test_non_summarize_query_with_same_chunks_still_disambiguates(self, monkeypatch):
        """Sanity: same store, non-SUMMARIZE phrasing → disambiguation
        fires as before. Confirms the bypass is intent-specific."""
        from core.src.env import config as env_cfg
        monkeypatch.delenv(env_cfg.GAP_THRESHOLD_ENV_VAR, raising=False)
        env_cfg._reset_retrieval_config_cache()

        triples = [
            ("R1", ["DOC_A"], 0.20),
            ("R2", ["DOC_B"], 0.21),
        ]
        g = nx.DiGraph()
        store = _ScriptedStore(_make_result(triples))
        p = QueryPipeline(
            graph=g, embedder=_FixedEmbedder(), store=store,
            enable_bm25=False, enable_grouping=True,
        )
        # Lookup-shaped query — SINGLE_DOC or GENERAL
        resp = p.query("What is the T3402 setting in DOC_A")
        assert resp.disambiguation_required is True
