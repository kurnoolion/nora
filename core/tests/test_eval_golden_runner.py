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
    assert captured["url"] == "http://127.0.0.1:9999/query"
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


# ─── Scope classification ───────────────────────────────────────────


def _miss(req_id, mno="", release=""):
    return {"req_id": req_id, "mno": mno, "release": release}


def test_scope_miss_when_entry_cell_not_searched():
    # The field case: entries name the older release; the stack searched
    # only the MNO's newer one.
    gt = [GroundTruthEntry(req_id="R-1", mno="mno-b", release="Jan2026")]
    misses = [_miss("R-1", "mno-b", "Jan2026")]
    scope, missing, extra = golden_runner.classify_scope(
        gt, misses, ["mno-b__Apr2026"]
    )
    assert scope == "miss+extra"
    assert missing == ["mno-b__Jan2026"]
    assert extra == ["mno-b__Apr2026"]
    assert misses[0]["miss_kind"] == "scope"


def test_retrieval_miss_when_cell_was_searched():
    gt = [
        GroundTruthEntry(req_id="R-1", mno="mno-a", release="Jan2026"),
        GroundTruthEntry(req_id="R-2", mno="mno-a", release="Jan2026"),
    ]
    misses = [_miss("R-2", "mno-a", "Jan2026")]
    scope, missing, extra = golden_runner.classify_scope(
        gt, misses, ["mno-a__Jan2026"]
    )
    assert (scope, missing, extra) == ("ok", [], [])
    assert misses[0]["miss_kind"] == "retrieval"


def test_extra_cells_are_informational():
    gt = [GroundTruthEntry(req_id="R-1", mno="mno-a", release="Jan2026")]
    scope, missing, extra = golden_runner.classify_scope(
        gt, [], ["mno-a__Jan2026", "mno-c__Jan2026"]
    )
    assert (scope, missing, extra) == ("extra", [], ["mno-c__Jan2026"])


def test_mno_only_entry_accepts_any_cell_of_its_mno():
    gt = [GroundTruthEntry(req_id="R-1", mno="mno-a")]
    misses = [_miss("R-1", "mno-a")]
    scope, _, extra = golden_runner.classify_scope(
        gt, misses, ["mno-a__Apr2026"]
    )
    assert scope == "ok" and extra == []
    assert misses[0]["miss_kind"] == "retrieval"
    misses = [_miss("R-1", "mno-a")]
    scope, missing, _ = golden_runner.classify_scope(
        gt, misses, ["mno-b__Apr2026"]
    )
    assert scope == "miss+extra" and missing == ["mno-a__*"]
    assert misses[0]["miss_kind"] == "scope"


def test_scope_not_applicable_without_cells_or_qualifiers():
    bare = [GroundTruthEntry(req_id="R-1")]
    misses = [_miss("R-1")]
    assert golden_runner.classify_scope(bare, misses, ["mno-a__Jan2026"]) \
        == ("n/a", [], [])
    # A bare entry cannot be attributed to a cell — counted as retrieval.
    assert misses[0]["miss_kind"] == "retrieval"
    qualified = [GroundTruthEntry(req_id="R-1", mno="mno-a", release="Jan2026")]
    misses = [_miss("R-1", "mno-a", "Jan2026")]
    assert golden_runner.classify_scope(qualified, misses, []) == ("n/a", [], [])
    assert misses[0]["miss_kind"] == ""


def test_run_stage1_records_scope_without_changing_recall(monkeypatch):
    # A version sample pinned to the older release: still strict on the
    # release, so the newer-release row is a miss — and a SCOPE miss.
    monkeypatch.setattr(
        golden_runner, "_post_json",
        lambda u, p, t: _service_body(
            [_row("R-1", 1, mno="mno-b", release="Apr2026")],
            resolved_cells=["mno-b__Apr2026"],
        ),
    )
    sample = _sample([
        GroundTruthEntry(req_id="R-1", mno="mno-b", release="Jan2026"),
    ])
    sample.kind = "version"
    r = run_stage1(sample, "http://127.0.0.1:9999")
    assert r.recall == 0.0
    assert r.scope == "miss+extra"
    d = r.to_dict()
    assert d["scope_missing_cells"] == ["mno-b__Jan2026"]
    assert d["misses"][0]["miss_kind"] == "scope"


def test_compact_report_scope_line():
    from core.src.eval.golden_runner import GoldenRunReport

    rep = GoldenRunReport(stack_label="s", stack_url="u", started_at="t")
    rep.stage1 = [
        Stage1Result(sample_id="gs-0001", recall=1.0, scope="ok"),
        Stage1Result(
            sample_id="gs-0002", recall=0.0, scope="miss+extra",
            misses=[{"req_id": "R-1", "miss_kind": "scope"},
                    {"req_id": "R-2", "miss_kind": "scope"}],
        ),
        Stage1Result(
            sample_id="gs-0003", recall=0.5, scope="extra",
            misses=[{"req_id": "R-3", "miss_kind": "retrieval"}],
        ),
    ]
    lines = rep.compact_report().splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("s1:"))
    assert lines[i + 1] == (
        "scope: ok=1 extra=1 miss=0 miss+extra=1 n/a=0 "
        "misses: scope=2 retrieval=1"
    )
    # NFR-8: counts only — no ids or cell names in the block.
    assert "R-1" not in "\n".join(lines) and "__" not in "\n".join(lines)


def test_compact_report_omits_scope_line_for_cellless_stacks():
    from core.src.eval.golden_runner import GoldenRunReport

    rep = GoldenRunReport(stack_label="s", stack_url="u", started_at="t")
    rep.stage1 = [Stage1Result(sample_id="gs-0001", recall=1.0)]
    assert not any(l.startswith("scope:")
                   for l in rep.compact_report().splitlines())


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

    def test_reasoning_effort_recorded_but_not_comparability_gating(self):
        """Deliberate: reasoning effort is stamped and printed, but two
        runs at different levels must still pool. Restricting the
        comparison is the analyst's call, not the tool's."""
        a = golden_runner.StackStamp.from_healthz(
            self._HEALTHZ, llm_identity="modelB", reasoning_effort="none")
        b = golden_runner.StackStamp.from_healthz(
            self._HEALTHZ, llm_identity="modelB", reasoning_effort="high")
        assert a.reasoning_effort == "none"
        assert b.reasoning_effort == "high"
        assert a.stage1_key() == b.stage1_key()
        assert a.stage2_key() == b.stage2_key()
        assert a.to_dict()["reasoning_effort"] == "none"

    def test_reasoning_effort_visible_on_the_identity_line(self):
        """Since it does not gate comparability, the compact line is the
        only place the difference shows — so it has to show."""
        s = golden_runner.StackStamp.from_healthz(
            self._HEALTHZ, reasoning_effort="none")
        assert "rsn=none" in s.compact_line()

    def test_unset_reasoning_leaves_the_line_unchanged(self):
        s = golden_runner.StackStamp.from_healthz(self._HEALTHZ)
        assert s.reasoning_effort == ""
        assert "rsn=" not in s.compact_line()

    def test_missing_healthz_yields_empty_stamp(self):
        s = golden_runner.StackStamp.from_healthz(None)
        assert s.stage1_key() == ("", "", (), "", "")
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
        assert "fb_pre=7" in line


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


# ─── Eval-set identity (field-found: golden set drifted mid-strand) ───


def _set_sample(sid, query="q?", status="golden-ready", gt=None, resp="golden"):
    from core.src.eval.golden import GoldenSample, GroundTruthEntry
    return GoldenSample(
        sample_id=sid, query=query, status=status,
        ground_truth=gt or [GroundTruthEntry(req_id=f"R-{sid}")],
        golden_response=resp,
    )


class TestEvalSetIdentity:
    def test_digests_stable_across_load_order(self):
        a = golden_runner.StackStamp()
        a.set_eval_set([_set_sample("s1"), _set_sample("s2")])
        b = golden_runner.StackStamp()
        b.set_eval_set([_set_sample("s2"), _set_sample("s1")])
        assert a.eval_set_count == 2
        assert a.eval_set_digest == b.eval_set_digest
        assert a.eval_set_stage2_digest == b.eval_set_stage2_digest

    def test_set_growth_breaks_stage1_comparability(self):
        # The field incident: curation landed mid-strand, 36 -> 38
        # scorable, byte-identical stack stamps scored different sets.
        a = golden_runner.StackStamp()
        a.set_eval_set([_set_sample("s1")])
        b = golden_runner.StackStamp()
        b.set_eval_set([_set_sample("s1"), _set_sample("s2")])
        assert a.stage1_key() != b.stage1_key()

    def test_draft_samples_do_not_count(self):
        a = golden_runner.StackStamp()
        a.set_eval_set([_set_sample("s1"), _set_sample("s2", status="draft")])
        assert a.eval_set_count == 1

    def test_response_recuration_breaks_stage2_only(self):
        # Two digests, not one: a golden-response edit must invalidate
        # Stage-2 comparability WITHOUT falsely invalidating Stage-1,
        # which never reads responses.
        a = golden_runner.StackStamp()
        a.set_eval_set([_set_sample("s1", resp="old response")])
        b = golden_runner.StackStamp()
        b.set_eval_set([_set_sample("s1", resp="new response")])
        assert a.stage1_key() == b.stage1_key()
        assert a.stage2_key() != b.stage2_key()

    def test_ground_truth_edit_breaks_both(self):
        from core.src.eval.golden import GroundTruthEntry
        a = golden_runner.StackStamp()
        a.set_eval_set([_set_sample("s1", gt=[GroundTruthEntry(req_id="R-1")])])
        b = golden_runner.StackStamp()
        b.set_eval_set([_set_sample("s1", gt=[GroundTruthEntry(req_id="R-2")])])
        assert a.stage1_key() != b.stage1_key()
        assert a.stage2_key() != b.stage2_key()

    def test_compact_line_and_dict_carry_set(self):
        s = golden_runner.StackStamp()
        s.set_eval_set([_set_sample("s1"), _set_sample("s2")])
        assert "set=2@" in s.compact_line()
        d = s.to_dict()
        assert d["eval_set_count"] == 2
        assert len(d["eval_set_digest"]) == 64
        assert len(d["eval_set_stage2_digest"]) == 64

    def test_run_all_freezes_set_into_stamp(self, monkeypatch):
        monkeypatch.setattr(
            golden_runner, "_get_json", lambda url, timeout: {})
        report = golden_runner.run_all(
            [_set_sample("s1", status="draft")],
            "http://127.0.0.1:1", "stackA", "2026-08-18T00:00:00",
        )
        # Draft-only set: count 0, digests empty — visible, not a guess.
        assert report.stamp.eval_set_count == 0
        assert report.stamp.eval_set_digest == ""


# ─── Release handling: kinds, release map, match rule m2 ──────────────


from core.src.eval.release_map import ReleaseMap  # noqa: E402


def _rmap(**mnos):
    return ReleaseMap(version=3, mnos={k.replace("_", "-"): v
                                       for k, v in mnos.items()})


def _cells_post(rows, cells):
    return lambda u, p, t: _service_body(rows, resolved_cells=cells)


def test_standard_sample_matches_across_releases(monkeypatch):
    # The field case under m2: entry authored on the older release, the
    # stack serves the newer one — same id, so it is a hit.
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("R-1", 2, mno="mno-b", release="Apr2026")], ["mno-b__Apr2026"]))
    sample = _sample([
        GroundTruthEntry(req_id="R-1", mno="mno-b", release="Jan2026"),
    ])
    r = run_stage1(sample, "http://127.0.0.1:9999",
                   release_map=_rmap(mno_b=["Jan2026", "Apr2026"]))
    assert r.recall == 1.0 and r.hits[0]["rank"] == 2
    assert r.scope == "ok"


def test_standard_sample_still_matches_mno(monkeypatch):
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("R-1", 1, mno="mno-c", release="Apr2026")], ["mno-c__Apr2026"]))
    sample = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b")])
    assert run_stage1(sample, "http://127.0.0.1:9999").recall == 0.0


def test_standard_scope_expects_map_n(monkeypatch):
    # A stack stuck on the older release: the id still matches (m2), but
    # the scope check against the map's N flags it.
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("R-1", 1, mno="mno-b", release="Jan2026")], ["mno-b__Jan2026"]))
    sample = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b",
                                       release="Jan2026")])
    r = run_stage1(sample, "http://127.0.0.1:9999",
                   release_map=_rmap(mno_b=["Jan2026", "Apr2026"]))
    assert r.recall == 1.0
    assert r.scope == "miss+extra"
    assert r.scope_missing_cells == ["mno-b__Apr2026"]


def test_version_sample_resolves_relative_releases(monkeypatch):
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("R-1", 1, mno="mno-b", release="Jan2026"),
         _row("R-2", 2, mno="mno-b", release="Apr2026")],
        ["mno-b__Apr2026", "mno-b__Jan2026"]))
    sample = _sample([
        GroundTruthEntry(req_id="R-1", mno="mno-b", release="N-1"),
        GroundTruthEntry(req_id="R-2", mno="mno-b", release="N"),
        GroundTruthEntry(req_id="R-1", mno="mno-b", release="N"),
    ])
    sample.kind = "version"
    r = run_stage1(sample, "http://127.0.0.1:9999",
                   release_map=_rmap(mno_b=["Jan2026", "Apr2026"]))
    assert {(h["req_id"], h["release"]) for h in r.hits} == {
        ("R-1", "Jan2026"), ("R-2", "Apr2026")}
    assert [(m["req_id"], m["release"], m["miss_kind"]) for m in r.misses] \
        == [("R-1", "Apr2026", "retrieval")]
    assert r.scope == "ok"


def test_relative_release_without_map_is_per_sample_error(monkeypatch):
    called = []
    monkeypatch.setattr(golden_runner, "_post_json",
                        lambda u, p, t: called.append(1))
    sample = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b",
                                       release="N")])
    sample.kind = "version"
    with pytest.raises(GoldenEvalError) as exc:
        run_stage1(sample, "http://127.0.0.1:9999")
    assert exc.value.code == "GEV-E005"
    assert called == []          # bound before any stack call
    with pytest.raises(GoldenEvalError) as exc:
        run_stage1(sample, "http://127.0.0.1:9999",
                   release_map=_rmap(mno_a=["Jan2026"]))
    assert exc.value.code == "GEV-E005"


def test_check_map_against_stack():
    rmap = _rmap(mno_a=["Jan2026"], mno_b=["Jan2026", "Apr2026"])
    hz = {"cells": ["mno-a__Jan2026", "mno-b__Jan2026"]}
    assert golden_runner.check_map_against_stack(
        rmap, hz, {"mno-a", "mno-b"}) == ["mno-b:Apr2026/Jan2026"]
    hz_ok = {"cells": ["mno-a__Jan2026", "mno-b__Jan2026", "mno-b__Apr2026"]}
    assert golden_runner.check_map_against_stack(
        rmap, hz_ok, {"mno-a", "mno-b"}) == []
    # Listed on one side only is a disagreement, named with "-"; listed
    # on neither is not (no expectation to disagree with).
    assert golden_runner.check_map_against_stack(
        rmap, {"cells": ["mno-a__Jan2026"]}, {"mno-b"}) == ["mno-b:Apr2026/-"]
    assert golden_runner.check_map_against_stack(
        rmap, {"cells": ["mno-a__Jan2026", "mno-c__Jan2026"]}, {"mno-c"}) \
        == ["mno-c:-/Jan2026"]
    assert golden_runner.check_map_against_stack(
        rmap, hz_ok, {"mno-c"}) == []
    # No cells advertised (legacy stack): nothing to check against.
    assert golden_runner.check_map_against_stack(rmap, {}, {"mno-b"}) == []


def _run_all(monkeypatch, healthz, rmap, **kw):
    monkeypatch.setattr(golden_runner, "fetch_healthz", lambda url: healthz)
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("R-1", 1, mno="mno-b", release="Apr2026")], ["mno-b__Apr2026"]))
    sample = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b",
                                       release="Jan2026")])
    sample.status = "stage1-ready"
    return golden_runner.run_all(
        [sample], "http://127.0.0.1:9999", "s", "t", release_map=rmap, **kw)


def test_run_all_refuses_map_stack_disagreement(monkeypatch):
    rmap = _rmap(mno_b=["Jan2026", "Apr2026"])
    with pytest.raises(GoldenEvalError) as exc:
        _run_all(monkeypatch, {"cells": ["mno-b__Jan2026"]}, rmap)
    assert exc.value.code == "GEV-E006"
    assert "mno-b:Apr2026/Jan2026" in exc.value.message


def test_run_all_allow_mismatch_stamps_it(monkeypatch):
    rmap = _rmap(mno_b=["Jan2026", "Apr2026"])
    rep = _run_all(monkeypatch, {"cells": ["mno-b__Jan2026"]}, rmap,
                   allow_map_mismatch=True)
    assert rep.stamp.release_map_mismatch == "mno-b:Apr2026/Jan2026"
    assert "rmap=v3!" in rep.stamp.compact_line()


def test_run_all_stamps_match_rule_and_map(monkeypatch):
    rmap = _rmap(mno_b=["Jan2026", "Apr2026"])
    rep = _run_all(monkeypatch, {"cells": ["mno-b__Apr2026"]}, rmap)
    line = rep.stamp.compact_line()
    assert "match=m3" in line and "rmap=v3" in line and "rmap=v3!" not in line
    assert rep.stamp.stage1_key()[-1] == "m3"
    assert rep.stage1[0].recall == 1.0
    # No map: still m2, no rmap field.
    rep = _run_all(monkeypatch, {"cells": ["mno-b__Apr2026"]}, None)
    assert "rmap=" not in rep.stamp.compact_line()
    assert rep.stamp.match_rule == "m3"


class TestDigestView:
    def _stamp(self, samples, rmap=None):
        st = golden_runner.StackStamp()
        st.set_eval_set(samples, rmap)
        return st.eval_set_digest

    def _std(self, release):
        s = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b",
                                      release=release)])
        s.status = "stage1-ready"
        return s

    def test_standard_provenance_retag_keeps_digest(self):
        assert self._stamp([self._std("Jan2026")]) == \
            self._stamp([self._std("Apr2026")])

    def test_kind_is_in_digest(self):
        a = self._std("Jan2026")
        b = self._std("Jan2026")
        b.kind = "version"
        assert self._stamp([a]) != self._stamp([b])

    def test_moving_n_rebinds_relative_version_samples(self):
        s = _sample([GroundTruthEntry(req_id="R-1", mno="mno-b",
                                      release="N")])
        s.kind, s.status = "version", "stage1-ready"
        v3 = _rmap(mno_b=["Jan2026"])
        v4 = _rmap(mno_b=["Jan2026", "Apr2026"])
        assert self._stamp([s], v3) != self._stamp([s], v4)
        # ...but a standard sample is unaffected by the same move.
        std = self._std("Jan2026")
        assert self._stamp([std], v3) == self._stamp([std], v4)


def test_m3_matches_ids_case_insensitively(monkeypatch):
    monkeypatch.setattr(golden_runner, "_post_json", _cells_post(
        [_row("REQ_FOO_0001", 1, mno="mno-b", release="Apr2026")],
        ["mno-b__Apr2026"]))
    sample = _sample([GroundTruthEntry(req_id="req_Foo_0001", mno="mno-b")])
    assert run_stage1(sample, "http://127.0.0.1:9999").recall == 1.0
    assert golden_runner.MATCH_RULE == "m3"
