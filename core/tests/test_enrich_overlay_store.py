"""Tests for the web-side enrichment-overlay store
(strand sira-enrichment-review D-DRAFT-1/2 semantics: word records,
replace-per-(word,direction), labels, reasons, reaffirm/discard)."""

from __future__ import annotations

import pytest

from core.src.web.enrich_overlay_store import (
    DEFAULT_REASON_CATEGORIES,
    EnrichOverlayStore,
)


@pytest.fixture()
def store(tmp_path):
    return EnrichOverlayStore(tmp_path)


def _edit(store, op, words=None, **kw):
    kw.setdefault("label", "camp1")
    kw.setdefault("reason", {"category": "too-generic", "note": ""})
    kw.setdefault("by", "expert")
    kw.setdefault("origin_release", "Feb2026")
    return store.edit("GP", "R1", op, words=words, **kw)


class TestEdits:
    def test_remove_creates_stamped_record(self, store):
        entry = _edit(store, "remove", ["handover"])
        (rec,) = entry["remove"]
        assert rec["word"] == "handover" and rec["label"] == "camp1"
        assert rec["reason"]["category"] == "too-generic"
        assert rec["origin"] == {"release": "Feb2026"} and rec["by"] == "expert"
        assert store.get_entry("GP", "R1") == entry  # persisted

    def test_one_active_record_per_word_direction(self, store):
        _edit(store, "remove", ["w"])
        entry = _edit(store, "remove", ["w"], label="camp2")
        assert len(entry["remove"]) == 1
        assert entry["remove"][0]["label"] == "camp2"  # replaced, re-stamped

    def test_unremove_drops_record_and_prunes_entry(self, store):
        _edit(store, "remove", ["w"])
        assert _edit(store, "unremove", ["w"]) is None
        assert store.get_entry("GP", "R1") is None

    def test_add_unadd(self, store):
        entry = _edit(store, "add", ["t3402", "t3410"])
        assert [r["word"] for r in entry["add"]] == ["t3402", "t3410"]
        entry = _edit(store, "unadd", ["t3402"])
        assert [r["word"] for r in entry["add"]] == ["t3410"]

    def test_suppress_roundtrip(self, store):
        entry = _edit(store, "suppress")
        assert entry["suppress_all"]["value"] is True
        assert _edit(store, "unsuppress") is None

    def test_discard_pairs(self, store):
        _edit(store, "remove", ["a"])
        _edit(store, "add", ["b"])
        _edit(store, "suppress")
        entry = _edit(store, "discard",
                      pairs=[{"direction": "remove", "word": "a"},
                             {"direction": "suppress_all", "word": ""}])
        assert "remove" not in entry and "suppress_all" not in entry
        assert [r["word"] for r in entry["add"]] == ["b"]

    def test_reaffirm_restamps_origin(self, store):
        _edit(store, "remove", ["a"], origin_release="Nov2025")
        entry = _edit(store, "reaffirm",
                      pairs=[{"direction": "remove", "word": "a"}],
                      origin_release="Feb2026", by="expert2")
        rec = entry["remove"][0]
        assert rec["origin"] == {"release": "Feb2026"} and rec["by"] == "expert2"
        assert rec["label"] == "camp1"  # label/reason preserved

    def test_unknown_op_rejected(self, store):
        with pytest.raises(ValueError):
            store.edit("GP", "R1", "nuke")

    def test_disabled_store_raises(self):
        s = EnrichOverlayStore("")
        with pytest.raises(RuntimeError):
            s.edit("GP", "R1", "remove", words=["x"])
        assert s.get_overlay("GP") == {} and not s.enabled


class TestLabelsAndReasons:
    def test_label_toggle(self, store):
        assert store.set_label_disabled("exp", True) == {"exp"}
        assert store.disabled_labels() == {"exp"}
        assert store.set_label_disabled("exp", False) == set()

    def test_label_counts(self, store):
        _edit(store, "remove", ["a", "b"])
        _edit(store, "add", ["c"], label="camp2")
        counts = store.label_counts()
        assert counts == {"camp1": 2, "camp2": 1}

    def test_delete_label_strips_records_everywhere(self, store):
        _edit(store, "remove", ["a"])
        _edit(store, "add", ["c"], label="camp2")
        store.set_label_disabled("camp1", True)
        n = store.delete_label("camp1")
        assert n == 1
        assert store.label_counts() == {"camp2": 1}
        assert store.disabled_labels() == set()

    def test_reason_categories_seed_and_add(self, store):
        assert store.reason_categories() == DEFAULT_REASON_CATEGORIES
        cats = store.add_reason_category("new-cat")
        assert "new-cat" in cats
        assert "new-cat" in store.reason_categories()  # persisted
        assert store.add_reason_category("new-cat") == cats  # idempotent


class TestPendingSignal:
    def test_overlay_mtime_zero_then_positive(self, store):
        assert store.overlay_mtime("GP") == 0.0
        _edit(store, "remove", ["a"])
        assert store.overlay_mtime("GP") > 0.0


class TestMergeLog:
    def test_merge_and_unmerge(self, store):
        assert store.accepted_labels() == set()
        assert store.set_label_merged("camp1", True) == {"camp1"}
        assert store.accepted_labels() == {"camp1"}
        assert store.set_label_merged("camp1", False) == set()

    def test_delete_label_clears_merge_log(self, store):
        _edit(store, "remove", ["a"])
        store.set_label_merged("camp1", True)
        store.delete_label("camp1")
        assert store.accepted_labels() == set()

    def test_digest_is_label_view_scoped(self, store):
        # an un-merged label's edits change ITS view digest, not main's
        base_main = store.overlay_digest("GP")
        _edit(store, "remove", ["a"])                    # label camp1
        assert store.overlay_digest("GP") == base_main   # main unaffected
        assert store.overlay_digest("GP", "camp1") != base_main
        # merging folds it into main's digest
        store.set_label_merged("camp1", True)
        assert store.overlay_digest("GP") != base_main


class TestServiceCompatibility:
    """The store's output must be consumable by the service-side fold."""

    def test_round_trip_through_apply_overlay(self, store):
        from sandbox.sira_query.enrich_overlay import apply_overlay_to_req
        _edit(store, "remove", ["handover"])
        _edit(store, "add", ["t3402"])
        entry = store.get_entry("GP", "R1")
        res = apply_overlay_to_req(["handover", "retry"], entry,
                                   {"", "camp1"}, lambda o, r: "ok", "R1")
        assert res.effective == ["retry", "t3402"]


class TestEditApi:
    """Route-level tests on an isolated FastAPI app (full web app wiring is
    exercised by the import in test_routes_registered)."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import core.src.web.routes.enrich_review as er

        monkeypatch.setattr(
            er, "_store", lambda: EnrichOverlayStore(tmp_path))
        app = FastAPI()
        app.include_router(er.router)
        return TestClient(app)

    def test_edit_roundtrip(self, client):
        body = {"mno": "GP", "req_id": "R1", "op": "remove",
                "words": ["handover"], "label": "c1",
                "reason": {"category": "too-generic", "note": ""},
                "by": "e", "origin_release": "Feb2026"}
        data = client.post("/api/enrich-review/edit", json=body).json()
        assert data["entry"]["remove"][0]["word"] == "handover"
        assert data["overlay_mtime"] > 0

    def test_edit_rejects_unknown_op(self, client):
        r = client.post("/api/enrich-review/edit",
                        json={"mno": "GP", "req_id": "R1", "op": "nuke"})
        assert r.status_code == 422

    def test_labels_and_reasons_endpoints(self, client):
        client.post("/api/enrich-review/edit",
                    json={"mno": "GP", "req_id": "R1", "op": "add",
                          "words": ["w"], "label": "c1",
                          "origin_release": "Feb2026"})
        assert client.get("/api/enrich-review/labels").json()["counts"] == {"c1": 1}
        client.post("/api/enrich-review/labels/merge",
                    json={"label": "c1", "merged": True})
        assert client.get("/api/enrich-review/labels").json()["accepted"] == ["c1"]
        d = client.post("/api/enrich-review/labels/delete",
                        json={"label": "c1"}).json()
        assert d["records_removed"] == 1 and d["counts"] == {}
        cats = client.post("/api/enrich-review/reasons",
                           json={"category": "x-cat"}).json()["categories"]
        assert "x-cat" in cats

    def test_unconfigured_store_is_503(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import core.src.web.routes.enrich_review as er
        app = FastAPI()
        app.include_router(er.router)

        class _Cfg:
            corrections_root = ""
        monkeypatch.setattr("core.src.web.app.config", _Cfg(), raising=False)
        c = TestClient(app)
        r = c.post("/api/enrich-review/edit",
                   json={"mno": "GP", "req_id": "R1", "op": "remove",
                         "words": ["x"]})
        assert r.status_code == 503


def test_routes_registered():
    from core.src.web.app import app
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/enrich-review/edit" in paths
    assert "/api/enrich-review/labels" in paths
