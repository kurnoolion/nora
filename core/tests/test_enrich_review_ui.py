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
    "rows": [
        {"req_id": "R1", "text": "**req_id**: R1\n**plan**: PlanA\nbody one",
         "plan": "PlanA", "llm_words": ["handover", "retry"],
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
        # R2's held record references a word no longer in the overlay -> hidden
        assert "correction(s) held" not in r.text

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
        assert "<s>handover</s>" in r.text          # ghost chip
        assert "retry" in r.text                     # untouched chip
        assert "corrections pending" in r.text       # OOB banner flipped

    def test_add_is_one_freeform_phrase(self, client):
        # commas/spaces/punctuation do NOT split — one Add = one enrichment
        r = self._edit(client, op="add", words="attach retry, per timer T3402")
        assert "attach retry, per timer T3402" in r.text
        r = self._edit(client, op="add", words="t3410")
        assert "t3410" in r.text
        r = self._edit(client, op="unadd", word="attach retry, per timer T3402")
        assert "attach retry, per timer" not in r.text and "t3410" in r.text

    def test_suppress_and_undo(self, client):
        r = self._edit(client, op="suppress")
        assert "suppressed" in r.text and "Undo" in r.text
        r = self._edit(client, op="unsuppress")
        assert "Suppress all" in r.text
        # accidental-click guard: the suppress form asks for confirmation
        assert "hx-confirm" in r.text and "Are you sure" in r.text

    def test_per_row_reason_and_note(self, client):
        r = client.get("/api/enrich-review/table",
                       params={"cell": "GP__Feb2026", "plan": "PlanA"})
        # one reason select + note input PER row (stamp bar no longer has them)
        assert r.text.count("row-reason") >= 3 and r.text.count("row-note") >= 3
        assert "misleading-enrichment" in r.text   # categories rendered in rows

    def test_dom_id_sanitized_for_composite_ids(self):
        from core.src.web.routes.enrich_review import _row_view
        view = _row_view({"req_id": "doc: spec.pdf §2", "llm_words": [],
                          "held": []}, None, set(), "Feb2026")
        assert view["dom_id"] == "doc__spec_pdf__2"
        assert view["req_id"] == "doc: spec.pdf §2"

    def test_unknown_op_422(self, client):
        assert self._edit(client, op="nuke").status_code == 422


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
                            lambda url, timeout: calls.append(url) or _R())
        monkeypatch.setattr(er, "_SIRA_URLS", ["http://a:8040", "http://b:8041"])
        r = client.post("/api/enrich-review/apply", data={"cell": "GP__Feb2026"})
        assert r.status_code == 200
        assert calls == ["http://a:8040/cells/GP__Feb2026/reload",
                         "http://b:8041/cells/GP__Feb2026/reload"]
        assert "in sync with serving" in r.text
        assert "ok" in r.text
