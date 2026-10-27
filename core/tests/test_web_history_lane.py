"""The Ask page's release-change lane (core/src/web/routes/history_lane.py
and the ask-stream route, strand rel-history): applicability, the SSE
event sequence with partials and a run id, the cancel route under the
team gate, and the stored feedback row. Fake History store and provider;
no network, no corpus text."""

from __future__ import annotations

import json
import re
import threading

import pytest

pytest.importorskip("fastapi")

from core.src.query.release_diff import ReleaseDiffConfig  # noqa: E402
from core.src.web.routes import history_lane, playground  # noqa: E402

RELS = ["Jan2031", "Apr2031", "Jul2031"]


def _req(rid, text, sec="1.1"):
    return {"req_id": rid, "section": sec, "title": f"title {rid}", "text": text,
            "priority": "Mandatory", "applicability": ["Phone"]}


class FakeHistory:
    def __init__(self, mnos=("mno-w",)):
        self.mnos = list(mnos)
        self.calls: list[tuple] = []

    def list_mnos(self):
        return list(self.mnos)

    def releases(self, mno):
        return list(RELS) if mno in self.mnos else []

    def changed_pairs(self, mno, rel_a, rel_b):
        self.calls.append((mno, rel_a, rel_b))
        return {
            "mno": mno, "from": rel_a, "to": rel_b,
            "plans": {
                "PLANA": {"plan_name": "Plan A", "present": {"from": True, "to": True},
                          "changed": [{"req_id": "RA1", "section": "1.1", "kinds": [],
                                       "old": {"title": "t", "text": "retry after 30 s", "priority": "Mandatory", "applicability": []},
                                       "new": {"title": "t", "text": "retry after 31 s", "priority": "Mandatory", "applicability": []}}],
                          "added": [_req("RA9", "new timer", "1.9")], "removed": [],
                          "renamed": [], "renumbered": [{"req_id": "RA5", "section": "3.2", "from": "2.2", "detail": "chapter"}],
                          "moved": []},
                "PLANB": {"plan_name": "Plan B", "present": {"from": True, "to": True},
                          "changed": [{"req_id": "RB1", "section": "2.1", "kinds": [],
                                       "old": {"title": "t", "text": "a", "priority": "", "applicability": []},
                                       "new": {"title": "t", "text": "b", "priority": "", "applicability": []}}],
                          "added": [], "removed": [], "renamed": [], "renumbered": [], "moved": []},
            },
            "absent_plans": {"PLANX": {"absent_in": rel_b}},
            "counts": {"changed": 2, "added": 1, "removed": 0, "renamed": 0, "renumbered": 1,
                       "moved": 0, "plans": 2, "absent_plans": 1},
        }


class EchoProvider:
    model = "echo-model"

    def __init__(self):
        self.last_call_stats = {"model": "echo-model"}
        self.calls = 0

    def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
        self.calls += 1
        ids = re.findall(r"\[([A-Z]+\d+)\]", prompt)
        return "changed: " + ", ".join(dict.fromkeys(ids))


class FakeFeedback:
    def __init__(self):
        self.rows: list[dict] = []

    async def record_qa(self, **kw):
        self.rows.append(kw)
        return len(self.rows)


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.split("\n\n"):
        name, data = "message", None
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        if data is not None:
            out.append((name, data))
    return out


@pytest.fixture()
def world(monkeypatch):
    from fastapi.testclient import TestClient
    from core.src.web.app import app

    hist = FakeHistory()
    prov = EchoProvider()
    fb = FakeFeedback()
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: hist)
    monkeypatch.setattr(history_lane, "ReleaseDiffConfig",
                        type("Cfg", (), {"from_env": staticmethod(
                            lambda env=None: ReleaseDiffConfig(max_batch_bytes=4096, parallel=2, enabled=True))}))
    monkeypatch.setattr(playground, "_history_provider", lambda *a, **k: prov)
    monkeypatch.setattr(playground, "_run_query_for_test",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrieval ran")))
    app.state.feedback_store = fb
    return {"client": TestClient(app), "hist": hist, "prov": prov, "fb": fb}


QUESTION = "What changed in the latest release compared to the previous one?"


def test_applies_only_to_release_diff_questions_with_history(monkeypatch):
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: FakeHistory())
    cfg = ReleaseDiffConfig(enabled=True)
    assert history_lane.history_diff_applies(QUESTION, config=cfg) is not None
    assert history_lane.history_diff_applies("What is the retry timer?", config=cfg) is None
    assert history_lane.history_diff_applies(QUESTION, config=ReleaseDiffConfig(enabled=False)) is None
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: None)
    assert history_lane.history_diff_applies(QUESTION, config=cfg) is None


def test_stream_runs_the_history_lane_with_partials_and_a_run_id(world):
    c, hist, prov, fb = world["client"], world["hist"], world["prov"], world["fb"]
    r = c.post("/api/test/ask-stream", data={"question": QUESTION, "lanes": ["sira"],
                                              "section": "merged", "user_name": "u"})
    assert r.status_code == 200
    ev = _events(r.text)
    names = [n for n, _ in ev]
    assert names[0] == "lanes" and ev[0][1]["lanes"] == ["history"] and ev[0][1]["run_id"]
    partials = [d for n, d in ev if n == "partial"]
    assert len(partials) == 3 and {p["plan"] for p in partials} == {"PLANA", "PLANB"}
    assert all(p["lane"] == "history" and p["total"] == 3 and p["html"] for p in partials)
    assert sorted(p["done"] for p in partials) == [1, 2, 3]
    assert any("HISTORY" in d["message"] or "History" in d["message"]
               for n, d in ev if n == "progress")
    done = [d for n, d in ev if n == "done"]
    assert len(done) == 1
    html = done[0]["html"]
    assert "HISTORY" in html and "Plan A" in html and "RA1" in html and "RA9" in html
    assert "renumbered" in html and "PLANX" in html and "not part of" in html
    assert "RHD mno=mno-w from=Apr2031 to=Jul2031" in html
    assert hist.calls == [("mno-w", "Apr2031", "Jul2031")] and prov.calls == 3
    assert len(fb.rows) == 1 and fb.rows[0]["lane"] == "history"
    assert fb.rows[0]["lane_config"]["batches"] == 3 and fb.rows[0]["cited_ids"]
    lc = fb.rows[0]["lane_config"]
    assert lc["counts"]["plans"] == 2 and lc["counts"]["absent_plans"] == 1
    assert set(lc["batch_ms"]) == {"p50", "p95", "max"} and lc["errors"] == [] and "elapsed_ms" in lc
    assert lc["retried"] == 0 and lc["stripped"] == 0
    assert "retry after" not in fb.rows[0]["lane_config"].__repr__()


def test_sira_lane_untouched_for_other_questions(world, monkeypatch):
    c = world["client"]
    ran = []

    async def fake_sira(question, request, **kw):
        ran.append(question)
        return {"error": "stubbed"}

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", fake_sira)
    r = c.post("/api/test/ask-stream", data={"question": "What is the retry timer?",
                                              "lanes": ["sira"], "section": "merged"})
    ev = _events(r.text)
    assert ev[0][1]["lanes"] == ["sira"] and ev[0][1]["run_id"] == "" and ran


def test_cancel_route_under_the_team_gate(world, monkeypatch):
    import core.src.web.team_mode as tm
    monkeypatch.setattr(tm, "TEAM_MODE", True)
    monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
    c = world["client"]
    run_id = history_lane.new_run_id()
    assert not history_lane.cancel_event(run_id).is_set()
    r = c.post("/api/test/ask-cancel", data={"run_id": run_id})
    assert r.status_code == 200 and r.json()["cancelled"] is True
    assert history_lane.cancel_event(run_id).is_set()
    assert c.post("/api/test/ask-cancel", data={"run_id": "nope"}).status_code == 404
    assert c.post("/api/test/ask-cancel", data={}).status_code == 400
    history_lane.forget_run(run_id)
    assert history_lane.cancel_event(run_id) is None


def test_lane_reports_coded_errors(monkeypatch):
    import asyncio
    intent = history_lane.classify(QUESTION)
    out = asyncio.run(history_lane.run_history_lane(
        QUESTION, intent, provider=EchoProvider(), run_id="x",
        config=ReleaseDiffConfig(enabled=True), history=None))
    assert out["error_code"] == "RHD-E003"

    class OneRelease(FakeHistory):
        def releases(self, mno):
            return ["Jan2031"]

    out = asyncio.run(history_lane.run_history_lane(
        QUESTION, intent, provider=EchoProvider(), run_id="x",
        config=ReleaseDiffConfig(enabled=True), history=OneRelease()))
    assert out["error_code"] == "RHD-E001"
    two = FakeHistory(mnos=("mno-w", "mno-v"))
    out = asyncio.run(history_lane.run_history_lane(
        QUESTION, intent, provider=EchoProvider(), run_id="x",
        config=ReleaseDiffConfig(enabled=True), history=two))
    assert "Name the MNO" in out["error"]


def test_answer_markdown_shape():
    result = {"cancelled": True, "plans": [
        {"plan_id": "P1", "plan_name": "One", "sections": [
            {"kind": "pair", "status": "ok", "text": "changed: R1", "ids": ["R1"]},
            {"kind": "added", "status": "cancelled", "text": "", "ids": ["R2"]},
            {"kind": "removed", "status": "error", "text": "", "ids": ["R3"]}],
         "notes": ["2 requirement(s) renumbered (2 chapter); content unchanged."]}],
        "absent_plans": [{"plan_id": "PX", "absent_in": "Jul2031"}]}
    md = history_lane.answer_markdown(result)
    assert md.startswith("_Cancelled") and "### P1 — One" in md and "changed: R1" in md
    assert "added: not synthesized (cancelled); ids: R2" in md
    assert "removed: synthesis failed (RHD-E004); ids: R3" in md
    assert "- 2 requirement(s) renumbered" in md and "PX: not part of Jul2031" in md
