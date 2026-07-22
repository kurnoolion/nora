"""Service-side tests for the enrichment-review surface
(strand sira-enrichment-review): overlay application pass, /cells/*
read endpoints, reload guardrails."""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient  # noqa: E402

import sandbox.sira_query.service as svc  # noqa: E402


class FakeBM25:
    """tokenize = whitespace-lower; enrich_batch records calls."""

    def __init__(self):
        self.enriched: list[tuple[int, list[str]]] = []

    def tokenize(self, text: str) -> list[str]:
        return text.lower().split()

    def enrich_batch(self, items):
        self.enriched.extend(items)


def _cell(mno, rel, corpus: dict[str, str], llm: dict[str, list[str]]):
    ids = list(corpus)
    st = svc.CellState(
        cell=(mno, rel), bm25=FakeBM25(), doc_ids=ids,
        doc_id_to_idx={r: i for i, r in enumerate(ids)},
        corpus_by_id={r: {"title": r, "text": t} for r, t in corpus.items()},
        max_df=10,
    )
    st.llm_words = dict(llm)
    return st


@pytest.fixture()
def two_release_cells(tmp_path, monkeypatch):
    """GP with two releases; R1 text unchanged across them, R2 changed."""
    same = "**plan**: PlanA\nthe device shall retry attach after timer expiry"
    feb = _cell("GP", "Feb2026",
                {"R1": same,
                 "R2": "**plan**: PlanB\ncompletely different requirement now",
                 "R3": "**plan**: PlanA\nrequirement sira gave no words for",
                 # composite rows stamp `plan_id / plan_name` (BM25 bridge)
                 "doc:PlanA": "**plan**: PlanA / Alpha Plan Doc\n# Alpha plan"},
                {"R1": ["handover", "retry"], "R2": ["roaming"]})
    nov = _cell("GP", "Nov2025",
                {"R1": same,
                 "R2": "**plan**: PlanB\nthe old text of requirement two"},
                {"R1": ["handover"]})
    monkeypatch.setattr(svc, "_cells", {("GP", "Feb2026"): feb,
                                        ("GP", "Nov2025"): nov})
    monkeypatch.setattr(svc, "_CELL_STATS_CACHE", {})

    d = tmp_path / "sira-enrich"
    d.mkdir()
    overlay = {
        "R1": {"remove": [{"word": "handover", "label": "",
                           "reason": {"category": "too-generic", "note": ""},
                           "by": "t", "at": "x",
                           "origin": {"release": "Nov2025"}}]},
        "R2": {"add": [{"word": "newword", "label": "",
                        "reason": {"category": "missing-synonym", "note": ""},
                        "by": "t", "at": "x",
                        "origin": {"release": "Nov2025"}}]},
    }
    (d / "GP.json").write_text(json.dumps(overlay))
    monkeypatch.setattr(svc, "_CORR_ROOT", str(tmp_path))
    return feb, nov


class TestOverlayPass:
    def test_cross_release_apply_and_hold(self, two_release_cells):
        feb, _ = two_release_cells
        svc._apply_overlay_and_enrich(feb)
        # R1 unchanged across releases -> Nov2025-origin remove APPLIES
        assert feb.effective_words.get("R1") == ["retry"]
        # R2 changed -> Nov2025-origin add is HELD, not applied
        assert feb.effective_words.get("R2") == ["roaming"]
        assert feb.held_by_id["R2"][0]["word"] == "newword"
        assert feb.overlay_applied == {"removes": 1, "adds": 0,
                                       "suppressed": 0, "held": 1}
        # enrich_batch got the EFFECTIVE sets
        applied = {feb.doc_ids[i]: ph for i, ph in feb.bm25.enriched}
        assert applied == {"R1": ["retry"], "R2": ["roaming"]}
        assert feb.loaded_at > 0

    def test_no_corr_root_is_legacy_behavior(self, two_release_cells, monkeypatch):
        feb, _ = two_release_cells
        monkeypatch.setattr(svc, "_CORR_ROOT", "")
        svc._apply_overlay_and_enrich(feb)
        assert feb.effective_words == {"R1": ["handover", "retry"],
                                       "R2": ["roaming"]}
        assert feb.overlay_applied["removes"] == 0 and not feb.held_by_id


class TestEndpoints:
    def test_plans_and_enrichments(self, two_release_cells):
        feb, _ = two_release_cells
        svc._apply_overlay_and_enrich(feb)
        c = TestClient(svc.app)
        # composite rows' `plan_id / plan_name` stamp is NOT a selectable plan
        assert c.get("/cells/GP__Feb2026/plans").json()["plans"] == ["PlanA", "PlanB"]
        data = c.get("/cells/GP__Feb2026/enrichments", params={"plan": "PlanA"}).json()
        # ALL corpus rows of the plan are listed — R3 has no enrichments but
        # must still appear (the expert may add words to it), and the doc:
        # composite matches PlanA via the plan_id part of its stamp
        assert [r["req_id"] for r in data["rows"]] == ["R1", "R3", "doc:PlanA"]
        row = data["rows"][0]
        assert row["req_id"] == "R1" and row["llm_words"] == ["handover", "retry"]
        assert row["effective"] == ["retry"] and not row["suppressed"]
        bare = data["rows"][1]
        assert bare["llm_words"] == [] and bare["effective"] == []
        assert not bare["suppressed"] and not bare["held"]
        assert data["loaded_at"] == feb.loaded_at

    def test_plan_matches_either_composite_part(self):
        # heading-mode reqs stamp plan_name, leading-mode reqs stamp plan_id;
        # the composite `plan_id / plan_name` stamp must match both filters
        assert svc._plan_matches("PA1 / Alpha Plan Doc", "PA1")
        assert svc._plan_matches("PA1 / Alpha Plan Doc", "Alpha Plan Doc")
        assert not svc._plan_matches("PA1 / Alpha Plan Doc", "PB2")
        assert svc._plan_matches("PlanA", "PlanA")

    def test_cells_carries_loaded_at_and_corrections(self, two_release_cells):
        feb, nov = two_release_cells
        svc._apply_overlay_and_enrich(feb)
        svc._apply_overlay_and_enrich(nov)
        c = TestClient(svc.app)
        rows = {(r["mno"], r["release"]): r for r in c.get("/cells").json()["cells"]}
        assert rows[("GP", "Feb2026")]["corrections"]["removes"] == 1
        assert rows[("GP", "Feb2026")]["loaded_at"] == feb.loaded_at

    def test_unknown_cell_404(self, two_release_cells):
        c = TestClient(svc.app)
        assert c.get("/cells/XX__Jan2020/plans").status_code == 404
        assert c.post("/cells/not-a-cell/reload").status_code == 404
