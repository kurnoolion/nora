"""Slice-5 tests: enrichment-review exports — the label x category report,
the prompt-fix scorecard, and the /api/enrich-review/export endpoint
(strand sira-enrichment-review). Builders are pure; the endpoint runs
against a real store on tmp_path with _service_get mocked."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.src.web.routes.enrich_review as er
from core.src.web.app import app
from core.src.web.enrich_report import (
    SAMPLE_REQ_IDS,
    build_report,
    build_scorecard,
    flatten_records,
)


def _rec(word, label="", category="", note="", by="e", origin="Feb2026"):
    return {"word": word, "label": label,
            "reason": {"category": category, "note": note},
            "by": by, "at": "2026-07-21T00:00:00+00:00",
            "origin": {"release": origin}}


OVERLAYS = {
    "GP": {
        "R1": {"remove": [_rec("handover", "c1", "too-generic"),
                          _rec("retry", "c1", "wrong-context", note="ambiguous timer")],
               "add": [_rec("t3402", "c1", "missing-acronym")]},
        "R2": {"remove": [_rec("handover", "c1", "too-generic")],
               "suppress_all": {"value": True, "word": "",
                                **{k: v for k, v in _rec("", "c2", "misleading-enrichment").items()
                                   if k != "word"}}},
    },
    "MB": {
        "R9": {"remove": [_rec("handover", "", "too-generic", origin="Nov2025")]},
    },
}


class TestFlatten:
    def test_rows_and_ordering(self):
        rows = flatten_records(OVERLAYS)
        # sorted by mno then req_id; remove before add; suppress_all last per entry
        assert [(r["mno"], r["req_id"], r["direction"], r["word"]) for r in rows] == [
            ("GP", "R1", "remove", "handover"),
            ("GP", "R1", "remove", "retry"),
            ("GP", "R1", "add", "t3402"),
            ("GP", "R2", "remove", "handover"),
            ("GP", "R2", "suppress_all", ""),
            ("MB", "R9", "remove", "handover"),
        ]

    def test_fields_carried(self):
        r = flatten_records(OVERLAYS)[1]
        assert r["category"] == "wrong-context" and r["note"] == "ambiguous timer"
        assert r["by"] == "e" and r["origin"] == "Feb2026"


class TestReport:
    def test_sections_and_counts(self):
        text = build_report(OVERLAYS, plans={("GP", "R1"): "PlanA",
                                             ("GP", "R2"): "PlanB"},
                            generated="2026-07-21 00:00Z")
        assert "records: 4 remove / 1 add / 1 suppress" in text
        assert "c1  ·  too-generic: 2" in text
        # handover: 3 reqs across 2 MNOs, 2 known plans
        assert "handover  ·  reqs=3  plans=2" in text
        assert "t3402  ·  reqs=1" in text
        assert "misleading-enrichment: 1" in text          # suppressions
        assert "GP · Feb2026: 5" in text                    # origin drift
        assert "MB · Nov2025: 1" in text
        assert '"ambiguous timer" (R1)' in text             # verbatim note

    def test_label_and_mno_scope(self):
        text = build_report(OVERLAYS, label="c1")
        assert "scope: label=c1" in text
        assert "records: 3 remove / 1 add / 0 suppress" in text
        text = build_report(OVERLAYS, mno="MB")
        assert "records: 1 remove / 0 add / 0 suppress" in text

    def test_sample_req_ids_capped(self):
        overlays = {"GP": {f"R{i:02d}": {"remove": [_rec("w")]}
                           for i in range(SAMPLE_REQ_IDS + 3)}}
        text = build_report(overlays)
        assert f"+3 more" in text

    def test_empty_scope(self):
        text = build_report({}, generated="g")
        assert "(no records in scope)" in text and "(none)" in text

    def test_service_note_rendered(self):
        text = build_report({}, service_note="plans omitted")
        assert "note: plans omitted" in text


class TestScorecard:
    def test_fixed_unfixed_absent(self):
        current = {("GP", "R1"): {"retry"},       # handover fixed, retry not
                   ("GP", "R2"): {"handover"}}    # still there -> unfixed
        # MB/R9 absent from current corpus
        text = build_scorecard(OVERLAYS, current_llm=current)
        assert "label (none): fixed 0 / 0 remove-records" in text
        assert "1 req(s) absent from current corpus" in text
        assert "label c1: fixed 1 / 3 remove-records (33%)" in text
        assert "unfixed: handover x1 (R2)" in text
        assert "unfixed: retry x1 (R1)" in text
        assert "TOTAL: fixed 1 / 3 (33%)  ·  absent 1" in text

    def test_only_remove_records_scored(self):
        text = build_scorecard({"GP": {"R1": {"add": [_rec("x")]}}})
        assert "(no remove-records in scope)" in text


SERVICE_CELLS = {"cells": [
    {"mno": "GP", "release": "Nov2025"},
    {"mno": "GP", "release": "Feb2026"},   # latest for GP
    {"mno": "MB", "release": "Feb2026"},
    {"mno": "XX", "release": "Feb2026"},   # no overlay -> not queried
]}
SERVICE_ENRICH = {
    "GP__Feb2026": {"rows": [
        {"req_id": "R1", "plan": "PlanA", "llm_words": ["retry"]},
        {"req_id": "R2", "plan": "PlanB", "llm_words": ["handover"]},
    ]},
    "MB__Feb2026": {"rows": []},
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import core.src.web.app as app_mod
    monkeypatch.setattr(app_mod.config, "corrections_root", str(tmp_path))
    from core.src.web.enrich_overlay_store import EnrichOverlayStore
    store = EnrichOverlayStore(tmp_path)
    store.edit("GP", "R1", "remove", words=["handover"], label="c1",
               reason={"category": "too-generic"}, by="e",
               origin_release="Feb2026")
    store.edit("GP", "R2", "remove", words=["handover"], label="c1",
               reason={"category": "too-generic"}, by="e",
               origin_release="Feb2026")
    store.edit("MB", "R9", "remove", words=["handover"], label="c1",
               reason={"category": "too-generic"}, by="e",
               origin_release="Nov2025")

    calls = []

    def fake_get(path, params=None):
        calls.append(path)
        if path == "/cells":
            return SERVICE_CELLS
        for cell, data in SERVICE_ENRICH.items():
            if path == f"/cells/{cell}/enrichments":
                return data
        raise AssertionError(f"unexpected service path {path}")

    monkeypatch.setattr(er, "_service_get", fake_get)
    c = TestClient(app)
    c.service_calls = calls
    return c


class TestExportEndpoint:
    def test_report_mode(self, client):
        r = client.get("/api/enrich-review/export")
        assert r.status_code == 200
        assert r.text.startswith("ENRICH-REVIEW REPORT")
        assert "records: 3 remove" in r.text
        assert "handover  ·  reqs=3  plans=2" in r.text     # plans from latest cells
        # only the LATEST release per overlay-MNO is queried; XX never is
        assert "/cells/GP__Feb2026/enrichments" in client.service_calls
        assert not any("Nov2025" in p or "XX" in p for p in client.service_calls)

    def test_scorecard_mode(self, client):
        r = client.get("/api/enrich-review/export",
                       params={"mode": "scorecard"})
        assert r.status_code == 200
        # GP/R1 fixed, GP/R2 unfixed, MB/R9 absent (empty latest rows)
        assert "label c1: fixed 1 / 2 remove-records (50%)" in r.text
        assert "unfixed: handover x1 (R2)" in r.text
        assert "1 req(s) absent" in r.text

    def test_mno_and_label_scope(self, client):
        r = client.get("/api/enrich-review/export", params={"mno": "MB"})
        assert "scope: mno=MB" in r.text
        assert "records: 1 remove" in r.text
        r = client.get("/api/enrich-review/export", params={"label": "nope"})
        assert "records: 0 remove" in r.text

    def test_report_degrades_when_service_down(self, client, monkeypatch):
        from fastapi import HTTPException

        def down(path, params=None):
            raise HTTPException(status_code=502, detail="sira-query unreachable")

        monkeypatch.setattr(er, "_service_get", down)
        r = client.get("/api/enrich-review/export")
        assert r.status_code == 200
        assert "note: sira-query unavailable" in r.text

    def test_scorecard_requires_service(self, client, monkeypatch):
        from fastapi import HTTPException

        def down(path, params=None):
            raise HTTPException(status_code=502, detail="sira-query unreachable")

        monkeypatch.setattr(er, "_service_get", down)
        r = client.get("/api/enrich-review/export",
                       params={"mode": "scorecard"})
        assert r.status_code == 502

    def test_unknown_mode_422(self, client):
        assert client.get("/api/enrich-review/export",
                          params={"mode": "csv"}).status_code == 422

    def test_download_header(self, client):
        r = client.get("/api/enrich-review/export", params={"download": 1})
        assert "attachment" in r.headers.get("content-disposition", "")

    def test_team_gate_allows_export(self):
        from core.src.web.team_mode import path_allowed_for_team
        assert path_allowed_for_team("/api/enrich-review/export")
