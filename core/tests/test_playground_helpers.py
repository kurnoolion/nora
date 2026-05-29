"""Tests for the merged-tab helpers in core/src/web/routes/playground.py:
citation flatten + lane_config snapshot (NORA + SIRA-pure path).

The async SIRA-fetch wrapper (`_snapshot_sira_lane_config`) is a thin
httpx layer over `_pick_sira_snapshot` — the pure picker carries the
substance and is tested directly.

Run: python -m pytest core/tests/test_playground_helpers.py
"""

from __future__ import annotations

import pytest

from core.src.web.routes.playground import (
    _SIRA_HEALTHZ_SNAPSHOT_KEYS,
    _flatten_cited_ids,
    _pick_sira_snapshot,
    _snapshot_nora_lane_config,
)


# ── _flatten_cited_ids ────────────────────────────────────────────────


def test_flatten_cited_ids_empty_input():
    assert _flatten_cited_ids([]) == []
    assert _flatten_cited_ids(None) == []


def test_flatten_cited_ids_extracts_req_ids_dedup_sorted():
    citations = [
        {"req_id": "R-Z", "title": "z"},
        {"req_id": "R-A", "title": "a"},
        {"req_id": "R-Z", "title": "duplicate"},
        {"req_id": "R-M"},
    ]
    assert _flatten_cited_ids(citations) == ["R-A", "R-M", "R-Z"]


def test_flatten_cited_ids_skips_malformed_entries():
    """Tolerant of mixed shapes — a single bad item shouldn't break the
    Ask's row-insertion path."""
    citations = [
        {"req_id": "R-OK"},
        None,
        {"title": "no req_id field"},
        {"req_id": None},
        {"req_id": ""},
        {"req_id": 42},          # non-string
        "not a dict",
        {"req_id": "R-ALSO-OK"},
    ]
    assert _flatten_cited_ids(citations) == ["R-ALSO-OK", "R-OK"]


# ── _pick_sira_snapshot ───────────────────────────────────────────────


def test_pick_sira_snapshot_keys_subset():
    """All known snapshot keys are pulled when present."""
    healthz = {k: f"v-{k}" for k in _SIRA_HEALTHZ_SNAPSHOT_KEYS}
    healthz["other_field"] = "ignored"
    healthz["ok"] = True
    snap = _pick_sira_snapshot(healthz)
    assert set(snap) == set(_SIRA_HEALTHZ_SNAPSHOT_KEYS)
    assert "other_field" not in snap


def test_pick_sira_snapshot_drops_missing_keys():
    """Keys absent from healthz are skipped (don't appear as None)."""
    healthz = {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "query_enrich_enabled": False,
        "fanout_enabled": False,
    }
    snap = _pick_sira_snapshot(healthz)
    assert snap == {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "query_enrich_enabled": False,
        "fanout_enabled": False,
    }


def test_pick_sira_snapshot_empty_healthz():
    assert _pick_sira_snapshot({}) == {}


def test_pick_sira_snapshot_preserves_value_types():
    """Booleans + numbers + nulls + strings round-trip — no JSON coercion
    in the picker itself; serialization happens at DB insert time."""
    healthz = {
        "corpus_size": 13974,
        "rerank_enabled": False,
        "expansion_weight": 0.0,
        "doc_enrich_run_pinned": "enrich-stable",
        "rerank_run_pinned": None,
    }
    snap = _pick_sira_snapshot(healthz)
    assert snap["corpus_size"] == 13974
    assert snap["rerank_enabled"] is False
    assert snap["expansion_weight"] == 0.0
    assert snap["doc_enrich_run_pinned"] == "enrich-stable"
    assert snap["rerank_run_pinned"] is None


# ── _snapshot_nora_lane_config ────────────────────────────────────────


def test_nora_snapshot_pulls_from_result_dict(monkeypatch):
    monkeypatch.delenv("NORA_LLM_MODEL", raising=False)
    monkeypatch.delenv("NORA_QUERY_RERANK_ENABLED", raising=False)
    monkeypatch.delenv("NORA_QUERY_BROAD_TOP_K", raising=False)
    monkeypatch.delenv("NORA_QUERY_NARROW_TOP_K", raising=False)
    monkeypatch.delenv("NORA_INCLUDE_PARENT_BODY", raising=False)
    result = {
        "llm_model": "internal-llm",
        "query_intent": "general",
        "candidate_count": 12,
        "answer": "...",       # extra fields ignored
    }
    snap = _snapshot_nora_lane_config(result)
    assert snap == {
        "llm_model": "internal-llm",
        "query_intent": "general",
        "candidate_count": 12,
    }


def test_nora_snapshot_includes_set_env_vars(monkeypatch):
    monkeypatch.setenv("NORA_LLM_MODEL", "internal-llm-v2")
    monkeypatch.setenv("NORA_QUERY_BROAD_TOP_K", "25")
    monkeypatch.setenv("NORA_QUERY_NARROW_TOP_K", "10")
    monkeypatch.delenv("NORA_QUERY_RERANK_ENABLED", raising=False)
    monkeypatch.delenv("NORA_INCLUDE_PARENT_BODY", raising=False)
    snap = _snapshot_nora_lane_config({"llm_model": "from-result"})
    assert snap["llm_model"] == "from-result"      # result dict wins for this field
    assert snap["NORA_LLM_MODEL"] == "internal-llm-v2"
    assert snap["NORA_QUERY_BROAD_TOP_K"] == "25"
    assert snap["NORA_QUERY_NARROW_TOP_K"] == "10"
    assert "NORA_QUERY_RERANK_ENABLED" not in snap
    assert "NORA_INCLUDE_PARENT_BODY" not in snap


def test_nora_snapshot_missing_result_fields_are_none(monkeypatch):
    """A query that didn't surface llm_model still gets a snapshot —
    the key exists with None so analysis can spot the gap."""
    monkeypatch.delenv("NORA_LLM_MODEL", raising=False)
    snap = _snapshot_nora_lane_config({})
    assert snap["llm_model"] is None
    assert snap["query_intent"] is None
    assert snap["candidate_count"] is None
