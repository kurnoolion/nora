"""Tests for core/src/store_service/enrich_overlay.py (sira-enrichment-review
D-DRAFT-1/2/3 semantics: word records, label-branch allowlist, remove-wins,
cross-release guard with held-not-applied)."""

from __future__ import annotations

import json

import pytest

from core.src.store_service.enrich_overlay import (
    JACCARD_THRESHOLD_DEFAULT,
    allowed_labels,
    apply_overlay_to_req,
    filter_overlay,
    jaccard,
    load_accepted_labels,
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
        r = apply_overlay_to_req(["a", "b"], None, None, ALWAYS_OK, "R1")
        assert r.effective == ["a", "b"] and not r.held and not r.suppressed

    def test_remove_and_add(self):
        entry = {"remove": [_rec("b")], "add": [_rec("z")]}
        r = apply_overlay_to_req(["a", "b"], entry, None, ALWAYS_OK, "R1")
        assert r.effective == ["a", "z"]
        assert r.applied_removes == ["b"] and r.applied_adds == ["z"]

    def test_remove_wins_over_add(self):
        # same timestamp -> tie -> legacy remove-wins bias
        entry = {"remove": [_rec("x", label="g1")], "add": [_rec("x", label="g2")]}
        r = apply_overlay_to_req(["a", "x"], entry, None, ALWAYS_OK, "R1")
        assert "x" not in r.effective

    def test_newer_add_countermands_older_remove(self):
        # an earlier (merged) label removed "x"; a later correction re-adds
        # it — the newer record wins; the original remove stays in the
        # overlay (read-only in branches) but no longer takes effect
        entry = {"remove": [_rec("x", label="g1")],
                 "add": [_rec("x", label="g2", at="2026-07-21T00:00:00Z")]}
        r = apply_overlay_to_req(["a", "x"], entry, None, ALWAYS_OK, "R1")
        assert r.effective == ["a", "x"]          # original position kept
        assert r.applied_removes == [] and r.applied_adds == ["x"]

    def test_newer_remove_still_beats_older_add(self):
        entry = {"remove": [_rec("x", at="2026-07-21T00:00:00Z")],
                 "add": [_rec("x")]}
        r = apply_overlay_to_req(["a", "x"], entry, None, ALWAYS_OK, "R1")
        assert "x" not in r.effective

    def test_suppress_all_keeps_only_adds(self):
        entry = {"remove": [], "add": [_rec("z")],
                 "suppress_all": {**_rec(""), "value": True}}
        r = apply_overlay_to_req(["a", "b"], entry, None, ALWAYS_OK, "R1")
        assert r.effective == ["z"] and r.suppressed

    def test_unallowed_label_records_are_inert_not_held(self):
        # allowlist: a record from a label OUTSIDE the view is invisible —
        # neither applied nor held (it belongs to someone else's branch)
        entry = {"remove": [_rec("a", label="exp")], "add": [_rec("z", label="exp")]}
        r = apply_overlay_to_req(["a"], entry, {""}, ALWAYS_OK, "R1")
        assert r.effective == ["a"] and not r.held

    def test_own_label_and_unlabeled_participate(self):
        entry = {"remove": [_rec("a", label="")],
                 "add": [_rec("z", label="exp")]}
        r = apply_overlay_to_req(["a"], entry, {"", "exp"}, ALWAYS_OK, "R1")
        assert r.effective == ["z"]

    def test_held_on_changed_verdict(self):
        entry = {"remove": [_rec("a", origin_release="Nov2025")]}
        verdict = lambda o, rid: "changed"  # noqa: E731
        r = apply_overlay_to_req(["a"], entry, None, verdict, "R1")
        assert r.effective == ["a"]  # not applied
        assert len(r.held) == 1 and r.held[0]["direction"] == "remove"

    def test_no_origin_applies(self):
        entry = {"remove": [{"word": "a", "label": ""}]}
        verdict = lambda o, rid: "changed"  # noqa: E731
        r = apply_overlay_to_req(["a"], entry, None, verdict, "R1")
        assert r.effective == []

    def test_add_no_duplicate_of_surviving_llm_word(self):
        entry = {"add": [_rec("a")]}
        r = apply_overlay_to_req(["a", "b"], entry, None, ALWAYS_OK, "R1")
        assert r.effective == ["a", "b"]


class TestLabelView:
    def test_allowed_labels_composition(self):
        assert allowed_labels(set()) == {""}
        assert allowed_labels({"m1"}, "exp") == {"", "m1", "exp"}
        assert allowed_labels({"m1"}) == {"", "m1"}

    def test_filter_overlay_projects_and_prunes(self):
        overlay = {
            "R1": {"remove": [_rec("a", label="exp"), _rec("b", label="m1")]},
            "R2": {"add": [_rec("z", label="other")]},
            "R3": {"suppress_all": {**_rec("", label="exp"), "value": True}},
        }
        out = filter_overlay(overlay, {"", "m1", "exp"})
        # R1 keeps only the allowed records; R2 vanishes entirely (pruned)
        assert [r["word"] for r in out["R1"]["remove"]] == ["a", "b"]
        assert "R2" not in out
        assert out["R3"]["suppress_all"]["value"] is True
        # projection is stable: filtering the SAME view twice is idempotent
        assert filter_overlay(out, {"", "m1", "exp"}) == out


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


class TestFingerprintVerdict:
    """store-split-design §4: a record whose fingerprint equals the loaded
    release's applies without the Jaccard guard; otherwise the guard runs
    as before; records without a fingerprint behave exactly as before."""

    # R1's text changed a lot between releases (Jaccard ~0) ...
    TOKENS = {("Jan2031", "R1"): {"a", "b", "c"},
              ("Oct2030", "R1"): {"x", "y", "z"}}
    # ... but the loaded release carries a known fingerprint for it
    FPS = {("Jan2031", "R1"): "fp-current"}

    def _verdict(self):
        return make_verdict_fn(lambda rel, rid: self.TOKENS.get((rel, rid)),
                               "Jan2031",
                               fingerprint_of=lambda rel, rid: self.FPS.get((rel, rid)))

    def _fold(self, rec):
        return apply_overlay_to_req(["a", "b"], {"remove": [rec]}, None,
                                    self._verdict(), "R1")

    def test_matching_fingerprint_applies_despite_low_jaccard(self):
        r = self._fold(_rec("b", origin_release="Oct2030", fingerprint="fp-current"))
        assert r.effective == ["a"] and not r.held

    def test_mismatched_fingerprint_falls_back_to_jaccard(self):
        r = self._fold(_rec("b", origin_release="Oct2030", fingerprint="fp-old"))
        assert r.effective == ["a", "b"]
        assert [h["word"] for h in r.held] == ["b"]
        assert r.held[0]["fingerprint"] == "fp-old"
        # ... and the Jaccard path still applies a similar req
        tokens = {("Jan2031", "R1"): {f"t{i}" for i in range(20)},
                  ("Oct2030", "R1"): {f"t{i}" for i in range(1, 20)}}
        v = make_verdict_fn(lambda rel, rid: tokens.get((rel, rid)), "Jan2031",
                            fingerprint_of=lambda rel, rid: "fp-current")
        assert v("Oct2030", "R1", "fp-old") == "ok"

    def test_mismatched_fingerprint_same_release_still_ok(self):
        v = self._verdict()
        assert v("Jan2031", "R1", "fp-old") == "ok"

    def test_no_fingerprint_is_old_behaviour(self):
        r = self._fold(_rec("b", origin_release="Oct2030"))
        assert r.effective == ["a", "b"] and len(r.held) == 1
        assert self._verdict()("Oct2030", "R1") == "changed"

    def test_unknown_current_fingerprint_falls_back(self):
        v = make_verdict_fn(lambda rel, rid: self.TOKENS.get((rel, rid)),
                            "Jan2031", fingerprint_of=lambda rel, rid: None)
        assert v("Oct2030", "R1", "fp-current") == "changed"

    def test_two_arg_verdict_fn_still_works_for_legacy_records(self):
        r = apply_overlay_to_req(["a", "b"], {"remove": [_rec("b", origin_release="Oct2030")]},
                                 None, ALWAYS_OK, "R1")
        assert r.effective == ["a"]


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
        assert load_accepted_labels(tmp_path) == set()

    def test_roundtrip(self, tmp_path):
        d = tmp_path / "sira-enrich"
        d.mkdir()
        (d / "GP.json").write_text(json.dumps({"R1": {"remove": [_rec("a")]}}))
        (d / "accepted-labels.json").write_text(json.dumps({"accepted": ["exp"]}))
        assert "R1" in load_overlay(tmp_path, "GP")
        assert load_accepted_labels(tmp_path) == {"exp"}

    def test_malformed_is_empty(self, tmp_path):
        d = tmp_path / "sira-enrich"
        d.mkdir()
        (d / "GP.json").write_text("{not json")
        assert load_overlay(tmp_path, "GP") == {}
