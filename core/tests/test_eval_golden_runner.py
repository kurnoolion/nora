"""Tests for golden Stage-1 runner: matching, recall, HTTP error mapping.

The service HTTP layer is stubbed by monkeypatching `_post_json` —
fixtures are synthetic, no proprietary content (NFR-8).
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from core.src.eval import golden_runner
from core.src.eval.golden import GoldenEvalError, GoldenSample, GroundTruthEntry
from core.src.eval.golden_runner import (
    Stage1Result,
    match_ground_truth,
    query_stack,
    recall_at,
    run_stage1,
)


def _row(req_id, rank, mno="mno-a", release="Jan2026"):
    return {"rank": rank, "req_id": req_id, "mno": mno, "release": release}


def _sample(entries) -> GoldenSample:
    return GoldenSample(
        sample_id="gs-0001", query="widget retry?", ground_truth=entries
    )


# ─── Matching ───────────────────────────────────────────────────────


def test_bare_entry_matches_any_cell():
    hits, misses = match_ground_truth(
        [GroundTruthEntry(req_id="REQ_FOO_0001")],
        [_row("REQ_FOO_0001", 3, mno="mno-b")],
    )
    assert misses == []
    assert hits[0]["rank"] == 3


def test_qualified_entry_requires_cell_match():
    entry = GroundTruthEntry(
        req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026"
    )
    hits, misses = match_ground_truth(
        [entry], [_row("REQ_FOO_0001", 1, mno="mno-b")]
    )
    assert hits == []
    assert misses[0]["req_id"] == "REQ_FOO_0001"
    hits, _ = match_ground_truth([entry], [_row("REQ_FOO_0001", 2)])
    assert hits[0]["rank"] == 2


def test_legacy_rows_without_cell_fields_match_on_req_id():
    entry = GroundTruthEntry(
        req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026"
    )
    hits, _ = match_ground_truth([entry], [{"rank": 1, "req_id": "REQ_FOO_0001"}])
    assert hits[0]["rank"] == 1


def test_best_rank_wins_across_duplicate_rows():
    hits, _ = match_ground_truth(
        [GroundTruthEntry(req_id="REQ_FOO_0001")],
        [_row("REQ_FOO_0001", 7, release="Apr2026"), _row("REQ_FOO_0001", 2)],
    )
    assert hits[0]["rank"] == 2


def test_plan_is_never_matched():
    entry = GroundTruthEntry(req_id="REQ_FOO_0001", plan="PLAN_Z")
    hits, _ = match_ground_truth([entry], [_row("REQ_FOO_0001", 1)])
    assert len(hits) == 1


def test_rank_falls_back_to_position():
    hits, _ = match_ground_truth(
        [GroundTruthEntry(req_id="REQ_FOO_0002")],
        [{"req_id": "REQ_FOO_0001"}, {"req_id": "REQ_FOO_0002"}],
    )
    assert hits[0]["rank"] == 2


def test_recall_at():
    hits = [{"rank": 2}, {"rank": 8}, {"rank": 15}]
    assert recall_at(hits, 4, 5) == pytest.approx(0.25)
    assert recall_at(hits, 4, 10) == pytest.approx(0.5)
    assert recall_at(hits, 4, 20) == pytest.approx(0.75)
    assert recall_at([], 0, 5) == 0.0


# ─── run_stage1 ─────────────────────────────────────────────────────


def _service_body(results, **kw):
    body = {
        "results": results,
        "top_k": 10,
        "effective_top_k": 20,
        "mode": "multi-cell",
        "resolved_cells": ["mno-a__Jan2026", "mno-b__Jan2026"],
    }
    body.update(kw)
    return body


def test_run_stage1_scores_and_carries_provenance(monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return _service_body(
            [_row("REQ_FOO_0001", 1), _row("REQ_FOO_0003", 9)]
        )

    monkeypatch.setattr(golden_runner, "_post_json", fake_post)
    sample = _sample([
        GroundTruthEntry(req_id="REQ_FOO_0001", mno="mno-a", release="Jan2026"),
        GroundTruthEntry(req_id="REQ_FOO_0002"),
        GroundTruthEntry(req_id="REQ_FOO_0003"),
        GroundTruthEntry(req_id="REQ_FOO_0004"),
    ])
    r = run_stage1(sample, "http://127.0.0.1:9999", top_k=20)
    assert captured["url"] == "http://127.0.0.1:9999/sira-query"
    assert captured["payload"] == {"query": "widget retry?", "top_k": 20}
    assert r.recall == pytest.approx(0.5)
    assert r.recall_at(5) == pytest.approx(0.25)
    assert r.recall_at(10) == pytest.approx(0.5)
    assert {m["req_id"] for m in r.misses} == {"REQ_FOO_0002", "REQ_FOO_0004"}
    assert r.mode == "multi-cell"
    assert r.effective_top_k == 20
    assert r.retrieved == 2
    d = r.to_dict()
    assert d["recall"] == 0.5
    assert d["recall_at_5"] == 0.25


def test_run_stage1_rejects_entryless_sample():
    with pytest.raises(GoldenEvalError) as exc:
        run_stage1(_sample([]), "http://127.0.0.1:9999")
    assert exc.value.code == "GEV-W001"


def test_query_stack_maps_transport_error(monkeypatch):
    def boom(url, payload, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(golden_runner, "_post_json", boom)
    with pytest.raises(GoldenEvalError) as exc:
        query_stack("http://127.0.0.1:9999", "q")
    assert exc.value.code == "GEV-E002"


def test_query_stack_rejects_missing_results(monkeypatch):
    monkeypatch.setattr(
        golden_runner, "_post_json", lambda u, p, t: {"detail": "oops"}
    )
    with pytest.raises(GoldenEvalError) as exc:
        query_stack("http://127.0.0.1:9999", "q")
    assert exc.value.code == "GEV-E002"


def test_query_stack_maps_bad_json(monkeypatch):
    def bad(url, payload, timeout):
        raise json.JSONDecodeError("bad", "", 0)

    monkeypatch.setattr(golden_runner, "_post_json", bad)
    with pytest.raises(GoldenEvalError) as exc:
        query_stack("http://127.0.0.1:9999", "q")
    assert exc.value.code == "GEV-E002"
