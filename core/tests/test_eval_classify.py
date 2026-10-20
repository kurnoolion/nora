"""Tests for the query-classification eval harness (strand
jev-experimentation, phase 1)."""

from __future__ import annotations

import json
import re

import pytest

from core.src.eval.classify import (
    ClassifySample,
    DEFAULT_SET_PATH,
    KNOB_NAMES,
    KNOB_PROFILE,
    divergence_cost,
    load_classify_set,
    profile_digest,
)
from core.src.query.analyzer import _PLAN_ALIASES
from core.src.query.pipeline import _TYPE_RERANK_ENABLED
from core.src.query.schema import DocTypeScope, QueryType


class TestClassifySet:
    def test_loads_default_set(self):
        samples = load_classify_set()
        assert len(samples) == 50
        assert all(isinstance(s, ClassifySample) for s in samples)

    def test_sample_ids_unique(self):
        samples = load_classify_set()
        ids = [s.sample_id for s in samples]
        assert len(set(ids)) == len(ids)

    def test_every_query_type_has_five_samples(self):
        samples = load_classify_set()
        counts = {t: 0 for t in QueryType}
        for s in samples:
            counts[s.query_type] += 1
        assert counts == {t: 5 for t in QueryType}

    def test_labels_are_enum_members(self):
        for s in load_classify_set():
            assert isinstance(s.query_type, QueryType)
            assert isinstance(s.doc_type_scope, DocTypeScope)

    def test_doc_type_scope_coverage(self):
        """All three scopes appear — a set that is all REQUIREMENTS
        cannot detect a scope regression."""
        scopes = {s.doc_type_scope for s in load_classify_set()}
        assert scopes == set(DocTypeScope)


class TestSetHygiene:
    """NFR-8 / CLAUDE.md: no proprietary identifiers in committed fixtures."""

    def _raw(self) -> str:
        return DEFAULT_SET_PATH.read_text(encoding="utf-8")

    def test_no_real_plan_codes(self):
        """Reads the real codes out of the analyzer rather than
        restating them here, so this test carries none itself."""
        raw = self._raw().upper()
        leaked = sorted({c for c in _PLAN_ALIASES.values() if c.upper() in raw})
        assert leaked == [], f"real plan codes in committed set: {leaked}"

    def test_requirement_ids_are_placeholders(self):
        raw = self._raw()
        for req_id in re.findall(r"VZ_REQ_\w+_\d+", raw):
            assert re.fullmatch(r"VZ_REQ_PLAN_[A-Z]_\d{4}", req_id), (
                f"{req_id} is not a placeholder requirement ID"
            )

    def test_set_is_query_text_only(self):
        """Each sample carries exactly the four declared fields — no
        answers, no chunk bodies, no retrieved content."""
        payload = json.loads(self._raw())
        for sample in payload["samples"]:
            assert set(sample) == {
                "sample_id", "query", "query_type", "doc_type_scope"
            }


class TestDivergenceCost:
    def test_identical_type_costs_nothing(self):
        for t in QueryType:
            assert divergence_cost(t, t) == 0.0

    def test_fact_summarize_is_maximal(self):
        """Design §6's catastrophic pair: tight top_k + strict 0.4
        threshold + rerank on, versus wide top_k + 0.7 + grouping off.
        All five knobs differ, so the cost is 1.0."""
        assert divergence_cost(QueryType.FACT, QueryType.SUMMARIZE) == 1.0
        assert divergence_cost(QueryType.SUMMARIZE, QueryType.FACT) == 1.0

    def test_cross_doc_feature_level_is_free(self):
        """Design §6's mild pair — both broad, both rewrite-enabled,
        same BM25 weight, same threshold, same grouping. The two select
        an identical retrieval configuration, so confusing them costs
        nothing downstream."""
        assert divergence_cost(
            QueryType.CROSS_DOC, QueryType.FEATURE_LEVEL
        ) == 0.0

    def test_symmetric(self):
        for a in QueryType:
            for b in QueryType:
                assert divergence_cost(a, b) == divergence_cost(b, a)

    def test_bounded(self):
        for a in QueryType:
            for b in QueryType:
                assert 0.0 <= divergence_cost(a, b) <= 1.0

    def test_profile_covers_every_type(self):
        assert set(KNOB_PROFILE) == set(QueryType)
        assert all(len(row) == len(KNOB_NAMES) for row in KNOB_PROFILE.values())

    def test_rerank_is_uniform_and_therefore_excluded(self):
        """The five knobs deliberately exclude rerank: pipeline's
        per-type map is empty by design, so every class shares
        _DEFAULT_RERANK_ENABLED and the column would add 0.0 to every
        pairwise distance. If an entry is ever added, this fails — and
        the profile then needs a sixth knob."""
        assert _TYPE_RERANK_ENABLED == {}
        assert "rerank" not in KNOB_NAMES

    def test_digest_is_stable_and_short(self):
        assert profile_digest() == profile_digest()
        assert len(profile_digest()) == 12
