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
        assert "misleading-enrichment" in r.text  # seeded categories

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

    def test_add_then_unadd(self, client):
        r = self._edit(client, op="add", words="t3402, t3410")
        assert "t3402" in r.text and "t3410" in r.text
        r = self._edit(client, op="unadd", word="t3402")
        assert "t3402" not in r.text and "t3410" in r.text

    def test_suppress_and_undo(self, client):
        r = self._edit(client, op="suppress")
        assert "suppressed" in r.text and "Undo" in r.text
        r = self._edit(client, op="unsuppress")
        assert "Suppress all" in r.text

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
