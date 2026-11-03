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


    def releases(self, mno):
        return ["Jan2031", "Apr2031", "Jul2031"]

    def req_history(self, mno, plan_id, req_id, from_rel="", to_rel=""):
        if req_id == "RA1":
            return [{"valid_from": "Jan2031", "valid_to": "Apr2031", "title": "t", "text": "retry after 30 s",
                     "priority": "Mandatory", "applicability": []},
                    {"valid_from": "Jul2031", "valid_to": None, "title": "t", "text": "retry after 31 s",
                     "priority": "Mandatory", "applicability": ["Phone"]}]
        if req_id == "RA9":
            return [{"valid_from": "Jul2031", "valid_to": None, "title": "n", "text": "new timer",
                     "priority": "Optional", "applicability": []}]
        return []


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


def test_stream_runs_the_history_lane_with_partials_and_a_run_id(world, monkeypatch):
    monkeypatch.setenv("NORA_STACK_NAME", "stack-t")
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
    assert "<strong>mno-w: Apr2031 → Jul2031</strong>" in html       # the compared releases lead
    # bubbles point at the side-by-side route, with the id's plan
    assert 'hx-get="/api/test/history/req/RA1?mno=mno-w&amp;from=Apr2031&amp;to=Jul2031&amp;plan=PLANA"' in html
    assert "changed in text" in html and "plan(s) with changes" in html
    assert hist.calls == [("mno-w", "Apr2031", "Jul2031")] and prov.calls == 3
    assert len(fb.rows) == 1 and fb.rows[0]["lane"] == "history"
    assert fb.rows[0]["lane_config"]["batches"] == 3 and fb.rows[0]["cited_ids"]
    lc = fb.rows[0]["lane_config"]
    assert lc["counts"]["plans"] == 2 and lc["counts"]["absent_plans"] == 1
    assert set(lc["batch_ms"]) == {"p50", "p95", "max"} and lc["errors"] == [] and "elapsed_ms" in lc
    assert lc["retried"] == 0 and lc["stripped"] == 0
    assert lc["stack"] == "stack-t"
    assert lc["bubble_plans"]["RA1"] == "PLANA" and "RA9" in lc["bubble_plans"]   # stored answers rebuild their bubbles
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
    assert '<div class="rc-head" markdown="1">\n\n_Cancelled' in md and "<summary>P1 — One · " in md and "changed: R1" in md
    assert '<details class="rc-plan" data-plan="P1" markdown="1">' in md and md.count("</details>") == 2   # the plan, its ids
    assert md.startswith('<div class="rel-change" data-mno="" data-from="" data-to="" markdown="1">')
    assert "<th>Plan</th><th>Changed</th>" in md and '<td class="rc-plan-name">P1 — One</td>' in md
    assert '<div class="rc-sec" data-kind="changed" markdown="1">' in md and "**Changed: 0 requirement(s)**" in md
    assert "added: not synthesized (cancelled); ids: R2" in md
    assert "removed: synthesis failed (RHD-E004); ids: R3" in md
    assert "- 2 requirement(s) renumbered" in md and "PX: not part of Jul2031" in md
    assert "**Structural changes only**" not in md        # a plain `notes` list renders as bookkeeping


def test_answer_markdown_summary_leads_and_structural_notes_are_grouped():
    result = {"cancelled": False, "absent_plans": [], "mno": "mno-q", "from": "Apr2031", "to": "Jul2031",
              "display": {"mno": "mno-q", "from": "Spring 31 (Apr2031)", "to": "Jul2031"},
              "counts": {"requirements_from": 120, "requirements_to": 124, "added": 5, "removed": 1,
                         "renumbered": 2, "moved": 1, "absent_plans": 1},
              "plans": [{"plan_id": "P1", "plan_name": "One",
                         "counts": {"changed": 3, "added": 5, "removed": 1, "renumbered": 2, "moved": 1,
                                    "dropped": 4, "same": 1, "bookkeeping": 4},
                         "sections": [{"kind": "pair", "status": "ok", "text": "For [R1], x.", "ids": ["R1"], "lead": ""},
                                      {"kind": "removed", "status": "ok", "text": "The plan loses a timer.",
                                       "ids": ["R6"], "lead": "Removed: [R6]"}],
                         "notes": ["x"], "note_groups": {
                             "structural": ["2 requirement(s) renumbered (2 sibling); content unchanged."],
                             "bookkeeping": ["3 requirement(s) differ only in the revision record: [R2], [R3], [R4].",
                                             "1 requirement(s) unchanged in meaning (wording, formatting or layout only): [R5]."]}}]}
    md = history_lane.answer_markdown(result)
    assert '<div class="rc-head" markdown="1">\n\n**mno-q: Spring 31 (Apr2031) → Jul2031**' in md
    # headline numbers are store-derived: changed = pairs sent (3); the model's marks (1) stated beside;
    # "another" = bookkeeping dropped by the lane (4 − 1)
    assert "120 requirement(s) in Spring 31 (Apr2031), 124 in Jul2031; 3 changed in text (the model judged 1 of them unchanged in meaning on this run); another 3 differ in bookkeeping only; 5 added, 1 removed, 2 renumbered, 1 moved; 1 plan(s) with changes, 1 plan(s) present in one release only." in md
    assert "**Removed: [R6]**" in md and md.index("**Removed: [R6]**") < md.index("The plan loses a timer.")
    assert md.index('<div class="rc-sec" data-kind="removed" markdown="1">') < md.index("**Removed: [R6]**")   # the lead is the section title
    assert md.count("**Changed: 3 requirement(s)**") == 1 and md.index("**Changed: 3") < md.index("For [R1], x.")
    assert "**Structural changes only**" in md and "- 2 requirement(s) renumbered" in md
    assert "<summary>5 requirement(s) with no change in meaning (bookkeeping only, and 1 the model judged unchanged on this run) · show</summary>" in md
    assert "<summary>P1 — One · 3 changed, 5 added, 1 removed</summary>" in md
    # counts are buttons with the column's summary as hover text; zero is plain
    assert ('<button type="button" class="rc-count" data-tab="changed" title="3 requirement(s) changed in text; '
            'the model judged 1 of them unchanged in meaning on this run.">3</button>') in md
    assert 'data-tab="removed" title="Removed after Spring 31 (Apr2031): R6.">1</button>' in md
    assert 'data-tab="structural" title="2 requirement(s) renumbered (2 sibling); content unchanged.">2</button>' in md
    assert ('data-tab="bookkeeping" title="4 with no change in meaning: 3 differ only in the revision record; '
            '1 unchanged in meaning (wording, formatting or layout only).">4</button>') in md
    assert md.startswith('<div class="rel-change" data-mno="mno-q" data-from="Spring 31 (Apr2031)" data-to="Jul2031" markdown="1">')
    assert md.index("Structural changes only") < md.index("**Bookkeeping only**") < md.index("no change in meaning (bookkeeping only")
    # the title is visible once the plan is open; the id list is the nested block, still collapsed
    assert md.index("**Bookkeeping only**") < md.index('<details class="rc-ids" markdown="1">', md.index("**Bookkeeping only**"))


def test_answer_markdown_orders_by_change_and_caps_id_lists():
    from core.src.web.markdown_render import render_markdown
    many = [f"R{i}" for i in range(1, 15)]
    result = {"cancelled": False, "absent_plans": [], "plans": [
        {"plan_id": "P1", "plan_name": "Small", "counts": {"changed": 1, "added": 0, "removed": 0,
                                                            "renumbered": 0, "moved": 0, "dropped": 3},
         "sections": [{"kind": "pair", "status": "ok", "text": "For [R1], x.", "ids": ["R1"]}],
         "notes": ["3 requirement(s) differ only in the revision record: [R2], [R3], [R4]."]},
        {"plan_id": "P2", "plan_name": "Big", "counts": {"changed": 5, "added": 2, "removed": 0,
                                                          "renumbered": 0, "moved": 0, "dropped": 0},
         "sections": [{"kind": "pair", "status": "cancelled", "text": "", "ids": many}], "notes": []}]}
    md = history_lane.answer_markdown(result)
    assert md.index('data-plan="P2"') < md.index('data-plan="P1"')         # table by change count
    assert md.index("<summary>P2 — Big · 5 changed, 2 added") < md.index("<summary>P1 — Small · 1 changed")
    assert "R10 and 4 more_" in md and "R11" not in md                      # capped status line
    assert md.count("**Changed: ") == 2                                     # one title per plan, before a cancelled pair too
    assert md.count('<span class="rc-zero">0</span>') == 8                  # four zero cells in each plan's row
    assert 'data-tab="bookkeeping" title="3 with no change in meaning: 3 differ only in the revision record.">3</button>' in md
    html = str(render_markdown(md))
    assert '<table class="rc-counts">' in html and html.count("<details") == 3   # two plans, one id list
    plan_open, title = html.index('<details class="rc-plan" data-plan="P1">'), html.index("<strong>Bookkeeping only</strong>")
    assert plan_open < title and "</details>" not in html[plan_open:title]   # title and its block sit inside the plan
    assert '<div class="rc-sec" data-kind="bookkeeping">' in html and '<div class="rc-body">' in html   # nested blocks rendered
    assert 'title="' in html and html.count('class="rc-count"') == 4         # 1+1 (P1), 2 (P2) non-zero counts
    assert "<li>3 requirement(s) differ only in the revision record" in html   # markdown inside details


def test_side_by_side_bubble_shows_both_releases(world):
    c = world["client"]
    r = c.get("/api/test/history/req/RA1", params={"mno": "mno-w", "plan": "PLANA",
                                                   "from": "Apr2031", "to": "Jul2031"})
    assert r.status_code == 200
    html = r.text
    assert "<th class=\"w-50\">Apr2031</th><th class=\"w-50\">Jul2031</th>" in html
    assert ">30</del> s" in html and ">31</ins> s" in html
    assert "applies to: Phone" in html                      # the attribute row, since it differs
    r = c.get("/api/test/history/req/RA9", params={"mno": "mno-w", "plan": "PLANA",
                                                   "from": "Apr2031", "to": "Jul2031"})
    assert r.status_code == 200 and "not present in Apr2031" in r.text and "new timer" in r.text
    r = c.get("/api/test/history/req/NOPE", params={"mno": "mno-w", "plan": "PLANA",
                                                    "from": "Apr2031", "to": "Jul2031"})
    assert r.status_code == 404
    assert c.get("/api/test/history/req/RA1").status_code == 404      # no releases named


def test_versions_at_picks_the_live_version_per_release(world):
    d = history_lane.versions_at(world["hist"], "mno-w", "PLANA", "RA1", "Jan2031", "Jul2031")
    assert d["old"]["text"] == "retry after 30 s" and d["new"]["text"] == "retry after 31 s"
    assert [op["op"] for op in d["diff"]["text"]] == ["eq", "del", "add", "eq"]
    d = history_lane.versions_at(world["hist"], "mno-w", "PLANA", "RA9", "Jan2031", "Apr2031")
    assert d["old"] is None and d["new"] is None



def test_answer_markdown_names_pairs_without_synthesis_text():
    def result(text, same):
        return {"cancelled": False, "absent_plans": [], "plans": [
            {"plan_id": "P1", "plan_name": "One", "model_same": same,
             "counts": {"changed": 3, "added": 0, "removed": 0, "renumbered": 0, "moved": 0,
                        "dropped": len(same), "same": len(same), "bookkeeping": len(same)},
             "sections": [{"kind": "pair", "status": "ok", "text": text, "ids": ["R1", "R2", "R3"], "lead": ""}],
             "notes": [], "note_groups": {"structural": [], "bookkeeping": []}}]}
    # an empty reply: the Changed section stays, naming the pairs it has no text for
    md = history_lane.answer_markdown(result("", []))
    assert '<div class="rc-sec" data-kind="changed" markdown="1">' in md and "**Changed: 3 requirement(s)**" in md
    assert "_changed: no synthesis text came back for 3 pair(s); ids: R1, R2, R3_" in md
    # all pairs marked unchanged: the section says so instead
    md = history_lane.answer_markdown(result("", ["R1", "R2", "R3"]))
    assert "_the model judged all 3 changed pair(s) unchanged in meaning on this run_" in md
    assert "no synthesis text" not in md
    # one marked unchanged, the rest without text
    md = history_lane.answer_markdown(result("", ["R2"]))
    assert "no synthesis text came back for 2 pair(s); ids: R1, R3_" in md
    # text present: no status line
    assert "no synthesis text" not in history_lane.answer_markdown(result("For [R1], x.", []))
