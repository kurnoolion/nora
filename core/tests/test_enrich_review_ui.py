"""Slice-3 tests: the /enrichment-review page + HTMX partials
(strand sira-enrichment-review). Service reads are mocked at the
_service_get seam; the overlay store runs for real on tmp_path."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import core.src.web.routes.enrich_review as er
from core.src.web.app import app


SERVICE_ROWS = {
    "cell": "GP__Feb2026", "plan": "PlanA", "loaded_at": time.time(),
    "enrich_model": "fooBar-32B",
    "rows": [
        {"req_id": "R1", "text": "**req_id**: R1\n**plan**: PlanA\nbody one",
         "plan": "PlanA", "llm_words": ["handover", "retry"],
         "index_words": ["body", "one", "planbase"],
         "effective": ["handover", "retry"], "suppressed": False, "held": []},
        {"req_id": "R2", "text": "**req_id**: R2\n**plan**: PlanA\nbody two",
         "plan": "PlanA", "llm_words": ["roaming"],
         "effective": ["roaming"], "suppressed": False,
         "held": [{"word": "oldword", "direction": "remove",
                   "label": "", "origin": {"release": "Nov2025"}}]},
        # sira produced nothing for R3 — row still shown, add box usable
        {"req_id": "R3", "text": "**req_id**: R3\n**plan**: PlanA\nbody three",
         "plan": "PlanA", "llm_words": [],
         "effective": [], "suppressed": False, "held": []},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import core.src.web.app as app_mod
    monkeypatch.setattr(app_mod.config, "corrections_root", str(tmp_path))

    def fake_get(path, params=None):
        params = params or {}
        if path.endswith("/enrichments"):
            rows = SERVICE_ROWS["rows"]
            if params.get("req_id"):
                rows = [r for r in rows if r["req_id"] == params["req_id"]]
            return {**SERVICE_ROWS, "rows": rows}
        if path.endswith("/plans"):
            return {"cell": "GP__Feb2026", "plans": ["PlanA"]}
        if path == "/cells":
            return {"cells": [{"mno": "GP", "release": "Feb2026"}]}
        raise AssertionError(f"unexpected service path {path}")

    monkeypatch.setattr(er, "_service_get", fake_get)
    return TestClient(app)


class TestPage:
    def test_page_renders(self, client):
        r = client.get("/enrichment-review")
        assert r.status_code == 200
        assert "stamp-bar" in r.text and "Enrichment Review" in r.text
        # reason/note moved into the rows — stamp bar keeps label + name only
        assert "misleading-enrichment" not in r.text
        assert 'name="label"' in r.text and 'name="by"' in r.text

    def test_team_gate_allows_review_surface(self):
        from core.src.web.team_mode import path_allowed_for_team
        assert path_allowed_for_team("/enrichment-review")
        assert path_allowed_for_team("/api/enrich-review/edit")


class TestTable:
    def test_table_renders_chips_and_held(self, client):
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        assert r.status_code == 200
        assert "handover" in r.text and "roaming" in r.text
        assert "R3" in r.text                      # unenriched row still listed
        assert "in sync with serving" in r.text     # no overlay yet
        # base-layer column: model-named header + expandable index words
        assert "fooBar-32B enrichments" in r.text
        assert "planbase" in r.text and "3 words" in r.text
        # R2's held record references a word no longer in the overlay -> hidden
        assert "correction(s) held" not in r.text

    def test_table_chunks_with_revealed_sentinel(self, client, monkeypatch):
        monkeypatch.setattr(er, "_TABLE_PAGE", 2)
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        # first chunk: R1+R2, header shows the full total, sentinel present
        assert "R1" in r.text and "R2" in r.text and "R3" not in r.text
        assert "3 reqs" in r.text
        assert 'hx-trigger="revealed"' in r.text and "offset=2" in r.text
        # continuation: rows-only fragment, remaining row, no more sentinel
        r2 = client.get("/api/enrich-review/table",
                        params={"cell": "GP__Feb2026", "plan": "PlanA",
                                "offset": 2})
        assert "R3" in r2.text and "er-head" not in r2.text
        assert 'hx-trigger="revealed"' not in r2.text

    def test_held_banner_shows_when_record_still_present(self, client, tmp_path):
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        store = EnrichOverlayStore(tmp_path)
        store.edit("GP", "R2", "remove", words=["oldword"],
                   origin_release="Nov2025", by="e")
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        assert "correction(s) held" in r.text and "Nov2025" in r.text
        assert "Re-affirm" in r.text and "Discard" in r.text


class TestRowEdit:
    def _edit(self, client, **form):
        base = {"cell": "GP__Feb2026", "plan": "PlanA", "req_id": "R1",
                "label": "c1", "reason_category": "too-generic",
                "reason_note": "", "by": "expert"}
        return client.post("/api/enrich-review/row-edit", data={**base, **form})

    def test_remove_renders_ghost_and_pending(self, client):
        r = self._edit(client, op="remove", word="handover")
        assert r.status_code == 200
        assert ">handover</s>" in r.text            # struck chip
        assert "#fd7e14" in r.text                   # orange box
        assert "undo removal" in r.text
        assert "retry" in r.text                     # untouched chip
        assert "corrections pending" in r.text       # OOB banner flipped

    def test_add_is_one_freeform_phrase(self, client):
        # commas/spaces/punctuation do NOT split — one Add = one enrichment
        r = self._edit(client, op="add", words="attach retry, per timer T3402")
        assert "attach retry, per timer T3402" in r.text
        r = self._edit(client, op="add", words="t3410")
        assert "t3410" in r.text
        assert "undo addition" in r.text and "↺" in r.text
        r = self._edit(client, op="unadd", word="attach retry, per timer T3402")
        assert "attach retry, per timer" not in r.text and "t3410" in r.text

    def test_suppress_and_undo(self, client):
        r = self._edit(client, op="suppress")
        assert "suppressed" in r.text and "Undo" in r.text
        r = self._edit(client, op="unsuppress")
        assert "Suppress all" in r.text
        # accidental-click guard: the suppress form asks for confirmation
        assert "hx-confirm" in r.text and "Are you sure" in r.text

    def test_pending_clears_when_all_edits_undone(self, client, tmp_path,
                                                  monkeypatch):
        # serving applied the (empty) overlay at load — its digest is the
        # store's digest before any edit
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        served = EnrichOverlayStore(tmp_path).overlay_digest("GP")
        base = er._service_get
        monkeypatch.setattr(
            er, "_service_get",
            lambda path, params=None: {**base(path, params),
                                       "overlay_digest": served})
        r = self._edit(client, op="remove", word="handover")
        assert "corrections pending" in r.text
        r = self._edit(client, op="unremove", word="handover")
        # everything undone → content matches serving → no Apply button
        assert "corrections pending" not in r.text
        assert "in sync with" in r.text

    def test_per_row_reason_and_note(self, client):
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        # one reason select + note input PER row (stamp bar no longer has them)
        assert r.text.count("row-reason") >= 3 and r.text.count("row-note") >= 3
        assert "misleading-enrichment" in r.text   # categories rendered in rows

    def test_dom_id_sanitized_for_composite_ids(self):
        from core.src.web.routes.enrich_review import _row_view
        view = _row_view({"req_id": "doc: spec.pdf §2", "llm_words": [],
                          "held": []}, None, {""}, "", "Feb2026")
        assert view["dom_id"] == "doc__spec_pdf__2"
        assert view["req_id"] == "doc: spec.pdf §2"

    def test_unknown_op_422(self, client):
        assert self._edit(client, op="nuke").status_code == 422

    def test_record_creating_ops_require_label(self, client):
        # corrections belong to a label (branch) — no label, no new record
        r = self._edit(client, op="remove", word="handover", label="")
        assert r.status_code == 422 and "label is required" in r.text
        assert self._edit(client, op="add", words="x", label="").status_code == 422
        # undo ops only delete records — they stay label-free
        self._edit(client, op="remove", word="handover")   # label c1
        r = self._edit(client, op="unremove", word="handover", label="")
        assert r.status_code == 200


class TestLabelBranches:
    def test_unmerged_label_hidden_from_main_view(self, client, tmp_path):
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        EnrichOverlayStore(tmp_path).edit("GP", "R1", "remove",
                                          words=["handover"], label="c9", by="e")
        # main view (no label): c9's un-merged edit is invisible
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        assert "</s>" not in r.text
        # c9's own view: the removal renders bright + undoable
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA",
                               "label": "c9"})
        assert ">handover</s>" in r.text and "undo removal" in r.text

    def test_merged_label_renders_muted_readonly(self, client, tmp_path):
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        store = EnrichOverlayStore(tmp_path)
        store.edit("GP", "R1", "remove", words=["handover"], label="c9", by="e")
        store.set_label_merged("c9", True)
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        # visible in main now — struck but muted and with no undo button
        assert ">handover</s>" in r.text and "in main" in r.text
        assert "undo removal" not in r.text

    def test_readd_countermand_renders_add_chip(self):
        from core.src.web.routes.enrich_review import _row_view
        older = {"word": "x", "label": "c1", "by": "e",
                 "at": "2026-07-20T00:00:00+00:00"}
        newer = {"word": "x", "label": "c2", "by": "e",
                 "at": "2026-07-21T00:00:00+00:00"}
        # newer add countermands the merged remove -> green chip renders
        v = _row_view({"req_id": "R", "llm_words": ["x"], "held": []},
                      {"remove": [older], "add": [newer]},
                      {"", "c1", "c2"}, "c2", "Feb2026")
        assert [a["word"] for a in v["adds"]] == ["x"] and v["n_added"] == 1
        # older (or tied) add loses -> hidden, as before
        v = _row_view({"req_id": "R", "llm_words": ["x"], "held": []},
                      {"remove": [newer], "add": [older]},
                      {"", "c1", "c2"}, "c2", "Feb2026")
        assert v["adds"] == [] and v["n_added"] == 0

    def test_merge_endpoint_is_admin_only(self, client, monkeypatch):
        r = client.post("/api/enrich-review/labels/merge",
                        json={"label": "c1", "merged": True})
        assert r.status_code == 200 and r.json()["accepted"] == ["c1"]
        monkeypatch.setattr(er, "is_admin", lambda req: False)
        assert client.post("/api/enrich-review/labels/merge",
                           json={"label": "c1", "merged": False}).status_code == 403
        assert client.post("/api/enrich-review/labels/delete",
                           json={"label": "c1"}).status_code == 403


class TestApply:
    def test_apply_reloads_all_configured_services(self, client, monkeypatch):
        calls = []

        class _R:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"loaded_at": time.time() + 60}

        monkeypatch.setattr(er.httpx, "post",
                            lambda url, params=None, timeout=None:
                            calls.append(url) or _R())
        monkeypatch.setattr(er, "_SIRA_URLS", ["http://a:8040", "http://b:8041"])
        r = client.post("/api/enrich-review/apply", data={"cell": "GP__Feb2026"})
        assert r.status_code == 200
        assert calls == ["http://a:8040/cells/GP__Feb2026/reload",
                         "http://b:8041/cells/GP__Feb2026/reload"]
        assert "in sync with serving" in r.text
        assert "ok" in r.text

    def test_apply_banner_reflects_primary_not_stale_secondary(
            self, client, tmp_path, monkeypatch):
        # a secondary sira-query on an older image reports a digest from a
        # stale formula with a LATER loaded_at — it must not wedge the
        # banner on "pending" when the primary reloaded in sync
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        good = EnrichOverlayStore(tmp_path).overlay_digest("GP")

        def fake_post(url, params=None, timeout=None):
            stale = url.startswith("http://b:")

            class _R:
                status_code = 200
                def raise_for_status(self):
                    pass
                def json(self):
                    return {"loaded_at": time.time() + (120 if stale else 60),
                            "overlay_digest": "0" * 64 if stale else good}
            return _R()

        monkeypatch.setattr(er.httpx, "post", fake_post)
        monkeypatch.setattr(er, "_SIRA_URLS", ["http://a:8040", "http://b:8041"])
        r = client.post("/api/enrich-review/apply", data={"cell": "GP__Feb2026"})
        assert "in sync with serving" in r.text
        assert "corrections pending" not in r.text


class TestApplyAll:
    def _with_cells(self, monkeypatch, cells):
        base = er._service_get

        def fake(path, params=None):
            if path == "/cells":
                return {"cells": cells}
            return base(path, params)

        monkeypatch.setattr(er, "_service_get", fake)

    def test_pending_cells_lists_only_stale(self, client, tmp_path,
                                            monkeypatch):
        from core.src.web.enrich_overlay_store import EnrichOverlayStore
        good = EnrichOverlayStore(tmp_path).overlay_digest("GP")
        self._with_cells(monkeypatch, [
            {"mno": "GP", "release": "Feb2026", "overlay_digest": good},
            {"mno": "GP", "release": "Nov2025", "overlay_digest": "0" * 64},
            # image predating the per-cell digest — skipped, never pending
            {"mno": "ZZ", "release": "Feb2026"},
        ])
        r = client.get("/api/enrich-review/pending-cells")
        assert r.json() == {"pending": ["GP__Nov2025"]}

    def test_apply_all_reloads_each_stale_cell_on_every_service(
            self, client, monkeypatch):
        # corrections are per-MNO: a merge stales every release at once,
        # and one Apply-all press must sweep them all on both stacks
        self._with_cells(monkeypatch, [
            {"mno": "GP", "release": "Feb2026", "overlay_digest": "1" * 64},
            {"mno": "GP", "release": "Nov2025", "overlay_digest": "2" * 64},
        ])
        calls = []

        class _R:
            def raise_for_status(self):
                pass
            def json(self):
                return {}

        monkeypatch.setattr(er.httpx, "post",
                            lambda url, params=None, timeout=None:
                            calls.append(url) or _R())
        monkeypatch.setattr(er, "_SIRA_URLS", ["http://a:8040", "http://b:8041"])
        r = client.post("/api/enrich-review/apply-all", json={"label": ""})
        assert r.json()["applied"] == ["GP__Feb2026", "GP__Nov2025"]
        assert calls == ["http://a:8040/cells/GP__Feb2026/reload",
                         "http://b:8041/cells/GP__Feb2026/reload",
                         "http://a:8040/cells/GP__Nov2025/reload",
                         "http://b:8041/cells/GP__Nov2025/reload"]
