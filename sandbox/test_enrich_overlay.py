"""Tests for sandbox/sira_query/enrich_overlay.py (sira-enrichment-review
D-DRAFT-1/2/3 semantics: word records, label filter, remove-wins,
cross-release guard with held-not-applied)."""

from __future__ import annotations

import json

import pytest

from sandbox.sira_query.enrich_overlay import (
    JACCARD_THRESHOLD_DEFAULT,
    apply_overlay_to_req,
    jaccard,
    load_disabled_labels,
    load_overlay,
    make_verdict_fn,
)


def _rec(word, label="", origin_release="Feb2026", **kw):
    return {"word": word, "label": label,
            "reason": {"category": "too-generic", "note": ""},
            "by": "t", "at": "2026-07-20T00:00:00Z",
            "origin": {"release": origin_release}, **kw}


ALWAYS_OK = lambda origin, rid: "ok"  # noqa: E731


class TestFold:
    def test_no_entry_passthrough(self):
        r = apply_overlay_to_req(["a", "b"], None, set(), ALWAYS_OK, "R1")
        assert r.effective == ["a", "b"] and not r.held and not r.suppressed

    def test_remove_and_add(self):
        entry = {"remove": [_rec("b")], "add": [_rec("z")]}
        r = apply_overlay_to_req(["a", "b"], entry, set(), ALWAYS_OK, "R1")
        assert r.effective == ["a", "z"]
        assert r.applied_removes == ["b"] and r.applied_adds == ["z"]

    def test_remove_wins_over_add(self):
        entry = {"remove": [_rec("x", label="g1")], "add": [_rec("x", label="g2")]}
        r = apply_overlay_to_req(["a", "x"], entry, set(), ALWAYS_OK, "R1")
        assert "x" not in r.effective

    def test_suppress_all_keeps_only_adds(self):
        entry = {"remove": [], "add": [_rec("z")],
                 "suppress_all": {**_rec(""), "value": True}}
        r = apply_overlay_to_req(["a", "b"], entry, set(), ALWAYS_OK, "R1")
        assert r.effective == ["z"] and r.suppressed

    def test_disabled_label_records_are_inert_not_held(self):
        entry = {"remove": [_rec("a", label="exp")], "add": [_rec("z", label="exp")]}
        r = apply_overlay_to_req(["a"], entry, {"exp"}, ALWAYS_OK, "R1")
        assert r.effective == ["a"] and not r.held

    def test_empty_label_never_disabled(self):
        entry = {"remove": [_rec("a", label="")]}
        r = apply_overlay_to_req(["a"], entry, {""}, ALWAYS_OK, "R1")
        assert r.effective == []

    def test_held_on_changed_verdict(self):
        entry = {"remove": [_rec("a", origin_release="Nov2025")]}
        verdict = lambda o, rid: "changed"  # noqa: E731
        r = apply_overlay_to_req(["a"], entry, set(), verdict, "R1")
        assert r.effective == ["a"]  # not applied
        assert len(r.held) == 1 and r.held[0]["direction"] == "remove"

    def test_no_origin_applies(self):
        entry = {"remove": [{"word": "a", "label": ""}]}
        verdict = lambda o, rid: "changed"  # noqa: E731
        r = apply_overlay_to_req(["a"], entry, set(), verdict, "R1")
        assert r.effective == []

    def test_add_no_duplicate_of_surviving_llm_word(self):
        entry = {"add": [_rec("a")]}
        r = apply_overlay_to_req(["a", "b"], entry, set(), ALWAYS_OK, "R1")
        assert r.effective == ["a", "b"]


class TestVerdictFn:
    def _tokens(self, table):
        return lambda rel, rid: table.get((rel, rid))

    def test_same_release_short_circuits(self):
        v = make_verdict_fn(self._tokens({}), "Feb2026")
        assert v("Feb2026", "R1") == "ok"

    def test_similar_ok_changed_below_threshold(self):
        twenty = {f"t{i}" for i in range(20)}
        table = {("Feb2026", "R1"): twenty,
                 ("Nov2025", "R1"): (twenty - {"t0"}),          # 19/20 = 0.95 -> ok
                 ("Jul2024", "R1"): {"x", "y"}}                  # ~0 -> changed
        v = make_verdict_fn(self._tokens(table), "Feb2026")
        assert v("Nov2025", "R1") == "ok"
        assert v("Jul2024", "R1") == "changed"
        # consistency with the published threshold constant:
        assert jaccard(table[("Feb2026", "R1")],
                       table[("Nov2025", "R1")]) >= JACCARD_THRESHOLD_DEFAULT

    def test_unknown_when_either_side_missing(self):
        v = make_verdict_fn(self._tokens({("Feb2026", "R1"): {"a"}}), "Feb2026")
        assert v("Nov2025", "R1") == "unknown"


class TestJaccard:
    def test_both_empty_is_one(self):
        assert jaccard(set(), set()) == 1.0

    def test_one_empty_is_zero(self):
        assert jaccard({"a"}, set()) == 0.0

    def test_identical_is_one(self):
        assert jaccard({"a", "b"}, {"a", "b"}) == 1.0


class TestLoaders:
    def test_missing_files_are_empty(self, tmp_path):
        assert load_overlay(tmp_path, "GP") == {}
        assert load_disabled_labels(tmp_path) == set()

    def test_roundtrip(self, tmp_path):
        d = tmp_path / "sira-enrich"
        d.mkdir()
        (d / "GP.json").write_text(json.dumps({"R1": {"remove": [_rec("a")]}}))
        (d / "labels.json").write_text(json.dumps({"disabled": ["exp"]}))
        assert "R1" in load_overlay(tmp_path, "GP")
        assert load_disabled_labels(tmp_path) == {"exp"}

    def test_malformed_is_empty(self, tmp_path):
        d = tmp_path / "sira-enrich"
        d.mkdir()
        (d / "GP.json").write_text("{not json")
        assert load_overlay(tmp_path, "GP") == {}
