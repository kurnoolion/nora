"""Tests for golden Stage-1 runner: matching, recall, HTTP error mapping.

The service HTTP layer is stubbed by monkeypatching `_post_json` —
fixtures are synthetic, no proprietary content (NFR-8).
"""

from __future__ import annotations

import json
import re
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


# ─── StackStamp (strand golden-eval — runtime-captured stack identity) ───


class TestStackStamp:
    _HEALTHZ = {
        "serve_label": "labelX",
        "data_fingerprint": "abcdef0123456789",
        "code_version": "deadbeefcafe0123",
        "sira_prompt_scheme": "per-plan-v2",
        "shim_model": "modelA",
        "refusal_fallback": {"configured": True, "used": 7},
    }

    def test_from_healthz_extracts_canonical_keys(self):
        s = golden_runner.StackStamp.from_healthz(self._HEALTHZ, top_k=8)
        assert s.serve_label == "labelX"
        assert s.data_fingerprint == "abcdef0123456789"
        assert s.code_version == "deadbeefcafe0123"
        assert s.sira_prompt_scheme == "per-plan-v2"
        assert s.llm_identity == "modelA"          # shim_model fallback
        assert s.retrieval_knobs == {"top_k_requested": 8}
        assert s.fallback_used_snapshot == 7

    def test_owned_knob_subdict_harvested_wholesale(self):
        # The service owns its knob list via the retrieval_knobs
        # sub-dict; the stamp copies it — a knob added serving-side
        # reaches comparability with no eval change.
        h = dict(self._HEALTHZ,
                 retrieval_knobs={"default_top_k": 8, "rerank_enabled": True})
        s = golden_runner.StackStamp.from_healthz(h, top_k=12)
        assert s.retrieval_knobs == {
            "default_top_k": 8, "rerank_enabled": True,
            "top_k_requested": 12,
        }

    def test_legacy_toplevel_knobs_harvested_as_fallback(self):
        # Stacks predating the sub-dict still publish knob values at the
        # healthz top level — the whitelist keeps their stamps from
        # carrying an empty knob set (the field-found false-comparable).
        h = dict(self._HEALTHZ, default_top_k=8, rerank_enabled=True,
                 expansion_weight=0.4)
        s = golden_runner.StackStamp.from_healthz(h)
        assert s.retrieval_knobs["default_top_k"] == 8
        assert s.retrieval_knobs["rerank_enabled"] is True
        assert s.retrieval_knobs["expansion_weight"] == 0.4

    def test_different_knobs_break_stage1_comparability(self):
        # Field-found failure: with identity fields empty AND knobs
        # empty, two different stacks compared equal. Knob harvest must
        # separate them even before the identity keys are served.
        a = golden_runner.StackStamp.from_healthz(
            {"retrieval_knobs": {"default_top_k": 8}})
        b = golden_runner.StackStamp.from_healthz(
            {"retrieval_knobs": {"default_top_k": 16}})
        assert a.stage1_key() != b.stage1_key()

    def test_caller_supplied_fields_win_over_healthz(self):
        s = golden_runner.StackStamp.from_healthz(
            self._HEALTHZ,
            llm_identity="modelB",
            sira_prompt_scheme="single-v1",
            answer_prompt_version="ap-3",
        )
        assert s.llm_identity == "modelB"
        assert s.sira_prompt_scheme == "single-v1"
        assert s.answer_prompt_version == "ap-3"

    def test_missing_healthz_yields_empty_stamp(self):
        s = golden_runner.StackStamp.from_healthz(None)
        assert s.stage1_key() == ("", "", ())
        assert s.compact_line() == "id: (unstamped)"

    def test_stage1_key_prefers_fingerprint_over_label(self):
        a = golden_runner.StackStamp(
            serve_label="labelX", data_fingerprint="fp1", code_version="c1")
        b = golden_runner.StackStamp(
            serve_label="labelX", data_fingerprint="fp2", code_version="c1")
        # Same (reused) label name, different content → NOT comparable.
        assert a.stage1_key() != b.stage1_key()
        # No fingerprint advertised → label is the (weaker) fallback key.
        c = golden_runner.StackStamp(serve_label="labelX", code_version="c1")
        d = golden_runner.StackStamp(serve_label="labelX", code_version="c1")
        assert c.stage1_key() == d.stage1_key()

    def test_stage2_key_adds_generation_axes(self):
        base = dict(data_fingerprint="fp", code_version="c")
        a = golden_runner.StackStamp(**base, answer_prompt_version="ap-1",
                                     llm_identity="m")
        b = golden_runner.StackStamp(**base, answer_prompt_version="ap-2",
                                     llm_identity="m")
        assert a.stage1_key() == b.stage1_key()
        assert a.stage2_key() != b.stage2_key()

    def test_knobs_affect_stage1_key(self):
        a = golden_runner.StackStamp(data_fingerprint="fp",
                                     retrieval_knobs={"top_k": 8})
        b = golden_runner.StackStamp(data_fingerprint="fp",
                                     retrieval_knobs={"top_k": 16})
        assert a.stage1_key() != b.stage1_key()

    def test_compact_line_shape(self):
        s = golden_runner.StackStamp.from_healthz(self._HEALTHZ, top_k=8)
        line = s.compact_line()
        assert line.startswith("id: fp=abcdef012345 ")
        assert "code=deadbeefcafe" in line
        assert "scheme=per-plan-v2" in line
        assert re.search(r"knobs=1@[0-9a-f]{8}", line)   # digest, not a dump
        assert "fb_used=7" in line


def test_run_all_builds_stamp_and_report_carries_it(monkeypatch):
    monkeypatch.setattr(
        golden_runner, "_get_json", lambda url, timeout: dict(
            TestStackStamp._HEALTHZ))
    monkeypatch.setattr(
        golden_runner, "_post_json", lambda url, payload, timeout: {
            "results": [{"req_id": "R-1", "rank": 1}]})
    report = golden_runner.run_all(
        [], "http://127.0.0.1:PORT".replace(":PORT", ":1"), "stackA",
        "2026-08-17T00:00:00",
        answer_prompt_version="ap-1",
    )
    assert report.stamp is not None
    assert report.stamp.data_fingerprint == "abcdef0123456789"
    assert report.stamp.answer_prompt_version == "ap-1"
    d = report.to_dict()
    assert d["stamp"]["code_version"] == "deadbeefcafe0123"
    assert any(l.startswith("id: fp=") for l in
               report.compact_report().splitlines())


def test_per_cell_fingerprint_map_captured_diagnostic_only():
    # Field-found (same shape as the knobs gap): the per-cell map was
    # published on healthz but not captured. It rides the stamp as a
    # diagnostic — NOT part of the comparability keys (the combined
    # fingerprint carries those); it answers "which cell changed".
    h = {"data_fingerprint": "fpX",
         "data_fingerprint_cells": {"c1": "aaa", "c2": "bbb"}}
    s = golden_runner.StackStamp.from_healthz(h)
    assert s.data_fingerprint_cells == {"c1": "aaa", "c2": "bbb"}
    assert s.to_dict()["data_fingerprint_cells"] == {"c1": "aaa", "c2": "bbb"}
    assert "cells=2" in s.compact_line()
    # Same combined fp, different map → still comparable (map is not a key).
    t = golden_runner.StackStamp.from_healthz(
        {"data_fingerprint": "fpX", "data_fingerprint_cells": {"c1": "zzz"}})
    assert s.stage1_key() == t.stage1_key()
