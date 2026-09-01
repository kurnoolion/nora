"""Per-question reasoning effort on the Ask page (Phase 1).

Covers the two seams the feature adds:
  - `QueryPipeline.query(synthesizer=...)` — a per-call synthesizer override,
    so a request can vary the LLM without rebuilding the cached pipeline.
  - `_form_reasoning` — validation of the level submitted by the Ask form.

No network: the store, embedder and synthesizers are all doubles.
"""

from __future__ import annotations

import networkx as nx

from core.src.query.pipeline import QueryPipeline
from core.src.query.schema import QueryResponse
from core.src.vectorstore.store_base import QueryResult
from core.src.web.routes.playground import _form_reasoning


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


class _NamedSynthesizer:
    """Records that it ran and stamps its name into the answer."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0

    def synthesize(self, context, intent) -> QueryResponse:
        self.calls += 1
        return QueryResponse(answer=f"answer from {self.name}", citations=[])


def _result() -> QueryResult:
    return QueryResult(
        ids=["req:R1"],
        documents=["text R1"],
        metadatas=[{
            "req_id": "R1",
            "plan_id": "PLAN",
            "mno": "VZW",
            "release": "2026",
            "section_number": "1.0",
            "zone_type": "",
            "feature_ids": [],
            "hierarchy_path": ["DOC"],
        }],
        distances=[0.2],
    )


def _pipeline(constructed: _NamedSynthesizer) -> QueryPipeline:
    return QueryPipeline(
        graph=nx.DiGraph(),
        embedder=_FixedEmbedder(),
        store=_ScriptedStore(_result()),
        enable_bm25=False,
        enable_grouping=False,
        synthesizer=constructed,
    )


class TestSynthesizerOverride:
    def test_constructed_synthesizer_used_by_default(self):
        """No override — behaviour is exactly what it was before."""
        constructed = _NamedSynthesizer("cached")
        resp = _pipeline(constructed).query("what are the R1 requirements")
        assert constructed.calls == 1
        assert "cached" in resp.answer

    def test_override_replaces_it_for_one_call(self):
        constructed = _NamedSynthesizer("cached")
        per_query = _NamedSynthesizer("per-query")
        pipeline = _pipeline(constructed)

        resp = pipeline.query(
            "what are the R1 requirements", synthesizer=per_query,
        )
        assert per_query.calls == 1
        assert constructed.calls == 0
        assert "per-query" in resp.answer

    def test_override_does_not_persist(self):
        """The override is per call — the next query falls back to the
        pipeline's own synthesizer, so one request's reasoning level can
        never leak into the next."""
        constructed = _NamedSynthesizer("cached")
        per_query = _NamedSynthesizer("per-query")
        pipeline = _pipeline(constructed)

        pipeline.query("first question about R1", synthesizer=per_query)
        resp = pipeline.query("second question about R1")

        assert per_query.calls == 1
        assert constructed.calls == 1
        assert "cached" in resp.answer


class TestFormReasoning:
    def test_accepts_known_levels(self):
        for level in ("none", "low", "medium", "high"):
            assert _form_reasoning({"reasoning": level}) == level

    def test_normalizes_case_and_whitespace(self):
        assert _form_reasoning({"reasoning": "  HIGH "}) == "high"

    def test_missing_or_blank_means_endpoint_default(self):
        assert _form_reasoning({}) == ""
        assert _form_reasoning({"reasoning": ""}) == ""
        assert _form_reasoning({"reasoning": "   "}) == ""

    def test_unknown_value_degrades_to_default(self):
        """A stale page or a hand-rolled POST must not fail the question."""
        assert _form_reasoning({"reasoning": "ludicrous"}) == ""
