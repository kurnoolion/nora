"""Tests for the requirement-ID lookup lane (strand sira-intent, D-DRAFT-8).

Covers:
- `parse_id_message`: only-ID messages (separators, duplicates, case, one
  release) vs anything with other words.
- `lookup`: latest release by default with the others listed, a named
  release wins, a named release that lacks the ID, unknown IDs.
- The ask-stream route: the lane takes the SIRA slot, no retrieval or
  model runs, the feedback row's lane_config, the team gate on; with the
  NORA_ID_LOOKUP switch off (the default) the message goes to SIRA.

IDs, plans and texts are placeholders; no corpus content.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.src.web import req_tree
from core.src.web.routes import id_lookup_lane as lane
from core.src.web.routes import playground
from core.src.web.routes.id_lookup_lane import IdMessage, parse_id_message

A, B, C = "VZ_REQ_PLANA_0001", "VZ_REQ_PLANA_0002", "VZ_REQ_PLANB_0009"


def _row(req_id, release, text="placeholder body", **kw):
    return {"req_id": req_id, "mno": "mno-w", "release": release, "plan": "PLANA",
            "plan_name": "Plan A", "doc_id": "doc", "title": "Placeholder title",
            "section_number": "1.2", "parent_section": "", "text": text, **kw}


CORPUS = {
    A: [_row(A, "Mar2031", "old body"), _row(A, "Sep2031", "new body"), _row(A, "Jun2031")],
    B: [_row(B, "Mar2031")],
}


def fake_find_req(env_dir, req_id, mno="", release="", ignore_case=False):
    assert ignore_case
    return list(CORPUS.get(req_id.upper(), []))


# ── parse ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, ids, release", [
    (A, (A,), ""),
    (f"  {A}, {B};\n{C}  ", (A, B, C), ""),
    (f"{A} {A.lower()}", (A,), ""),
    (f"{A} Mar2031", (A,), "Mar2031"),
    (f"{A}, {B} sep 2031", (A, B), "Sep2031"),
])
def test_parse_accepts_only_ids_and_one_release(text, ids, release):
    assert parse_id_message(text) == IdMessage(ids, release)


@pytest.mark.parametrize("text", [
    f"What does {A} say about the retry counter?",
    f"{A} please",
    f"{A} Mar2031 Sep2031",
    "What is the T3402 value?",
    "",
    "Mar2031",
])
def test_parse_rejects_questions_and_non_id_messages(text):
    assert parse_id_message(text) is None


# ── lookup ────────────────────────────────────────────────────────────

def test_latest_release_by_default_with_the_others_listed():
    got = lane.lookup(None, IdMessage((A, B), ""), fake_find_req)
    a, b = got["entries"]
    assert a["req"]["release"] == "Sep2031" and a["req"]["text"] == "new body"
    assert a["also_in"] == ["Mar2031", "Jun2031"]
    assert b["req"]["release"] == "Mar2031" and b["also_in"] == []
    assert got["not_found"] == []


def test_named_release_wins_and_a_missing_one_says_where_it_is():
    got = lane.lookup(None, IdMessage((A, B), "Jun2031"), fake_find_req)
    a, b = got["entries"]
    assert a["req"]["release"] == "Jun2031" and a["also_in"] == ["Mar2031", "Sep2031"]
    assert b == {"req_id": B, "missing_in": "Jun2031", "also_in": ["Mar2031"]}


def test_unknown_ids_are_listed_and_the_answer_text_is_fenced():
    got = lane.lookup(None, IdMessage((A, C), ""), fake_find_req)
    assert got["not_found"] == [C]
    md = lane.answer_markdown(got)
    assert "```text\nnew body\n```" in md and "Also in: Mar2031, Jun2031" in md
    assert f"**Not found:** {C}" in md


def test_backticks_in_the_text_stay_inside_the_fence():
    # A stored answer is re-rendered as markdown (share link, History page):
    # a ``` line in the text must not close the fence early.
    from core.src.web.markdown_render import render_markdown
    text = "line one\n```\n**not bold**\n````\nlast"
    found = {"entries": [{"req_id": A, "req": _row(A, "Sep2031", text=text), "also_in": []}],
             "not_found": []}
    html = str(render_markdown(lane.answer_markdown(found)))
    assert "<strong>not bold</strong>" not in html and "**not bold**" in html
    assert "last" in html.split("</code>")[0]


def test_lane_output_shape_and_lane_config():
    out = asyncio.run(lane.run_id_lookup_lane(f"{A} {C}", IdMessage((A, C), ""),
                                              env_dir=None, find_req=fake_find_req))
    assert out["retrieved_ids"] == [A] and out["cited_ids"] == [A]
    assert out["result"]["llm_model"] == "" and out["result"]["rag_chunks"] == []
    lc = out["lane_config"]
    assert lc["lane"] == "id_lookup" and lc["intent"] == "other"
    assert lc["ids"] == [A, C] and lc["not_found"] == [C]
    assert lc["shown"] == [{"req_id": A, "release": "Sep2031"}]
    assert "new body" not in json.dumps(lc)            # no corpus text in the logged config


def test_store_failure_is_a_lane_error():
    def boom(*a, **k):
        raise OSError("down")
    out = asyncio.run(lane.run_id_lookup_lane(A, IdMessage((A,), ""), env_dir=None, find_req=boom))
    assert out == {"error": "Requirement lookup failed: OSError"}


# ── the route ─────────────────────────────────────────────────────────

class FakeFeedback:
    def __init__(self):
        self.rows = []

    async def record_qa(self, **kw):
        self.rows.append(kw)
        return len(self.rows)


def _events(text):
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

    fb = FakeFeedback()
    monkeypatch.setenv("NORA_ID_LOOKUP", "on")
    monkeypatch.setattr(req_tree, "find_req", fake_find_req)

    async def no_sira(*a, **k):
        raise AssertionError("SIRA lane ran")

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", no_sira)
    monkeypatch.setattr(playground.history_lane, "history_diff_applies",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("history checked")))
    app.state.feedback_store = fb
    return {"client": TestClient(app), "fb": fb}


def _ask(client, question):
    r = client.post("/api/test/ask-stream", data={"question": question, "lanes": ["sira"],
                                                   "section": "merged", "user_name": "u"})
    assert r.status_code == 200
    return _events(r.text)


def test_stream_answers_an_id_message_from_the_trees_store(world):
    ev = _ask(world["client"], f"{A}, {C}")
    assert ev[0] == ("lanes", {"lanes": ["id_lookup"], "run_id": ""})
    html = [d for n, d in ev if n == "done"][0]["html"]
    assert "LOOKUP" in html and "new body" in html and "Also in: Mar2031, Jun2031" in html
    assert 'title="Answer intent">lookup</span>' in html
    assert "Not found in the parsed corpus" in html and C in html
    row = world["fb"].rows[0]
    assert row["lane"] == "id_lookup" and row["lane_config"]["lane"] == "id_lookup"
    assert row["lane_config"]["not_found"] == [C]


def test_stream_stores_the_answer_in_the_real_feedback_store(world, tmp_path):
    # The FakeFeedback above accepts any lane; the real store validates it.
    import sqlite3
    from core.src.web.app import app
    from core.src.web.feedback_db import FeedbackStore

    store = FeedbackStore(tmp_path / "feedback.db")
    asyncio.run(store.initialize())
    app.state.feedback_store = store
    _ask(world["client"], f"{A}, {C}")
    with sqlite3.connect(tmp_path / "feedback.db") as db:
        rows = db.execute("SELECT id, lane, lane_config FROM test_feedback").fetchall()
    assert len(rows) == 1
    row_id, lane_name, lc = rows[0]
    assert lane_name == "id_lookup" and json.loads(lc)["not_found"] == [C]
    shared = world["client"].get(f"/ask/s/{row_id}")
    assert shared.status_code == 200 and A in shared.text


def test_stream_with_the_team_gate_on(world, monkeypatch):
    import core.src.web.team_mode as tm
    monkeypatch.setattr(tm, "TEAM_MODE", True)
    monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
    ev = _ask(world["client"], A)
    assert ev[0][1]["lanes"] == ["id_lookup"]
    assert "new body" in [d for n, d in ev if n == "done"][0]["html"]


@pytest.mark.parametrize("value", [None, "", "off", "false", "0"])
def test_switch_off_sends_an_id_message_to_sira(world, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("NORA_ID_LOOKUP", raising=False)
    else:
        monkeypatch.setenv("NORA_ID_LOOKUP", value)
    ran = []

    async def fake_sira(question, request, **kw):
        ran.append(question)
        return {"error": "stubbed"}

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", fake_sira)
    monkeypatch.setattr(playground.history_lane, "history_diff_applies", lambda *a, **k: None)
    ev = _ask(world["client"], A)
    assert ev[0][1]["lanes"] == ["sira"] and ran == [A]
    assert world["fb"].rows == []


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " ON "])
def test_switch_values_that_turn_it_on(monkeypatch, value):
    monkeypatch.setenv("NORA_ID_LOOKUP", value)
    assert lane.enabled()


# ── IDs named inside a question (pinning) ─────────────────────────────

def test_named_ids_finds_ids_anywhere_and_one_release():
    assert lane.named_ids(f"What does {A} say vs {B.lower()} and {A}?") == ([A, B], "")
    assert lane.named_ids(f"Explain {A} in Mar2031") == ([A], "Mar2031")
    assert lane.named_ids(f"{A} Mar2031 vs Sep2031") == ([A], "")
    assert lane.named_ids("What is the T3402 value?") == ([], "")


def test_pinned_candidates_latest_or_named_release_and_capped():
    ids, cands = lane.pinned_candidates(None, f"What does {A} and {C} require?",
                                        text_chars=3, find_req=fake_find_req)
    assert ids == [A, C] and [c["req_id"] for c in cands] == [A]
    assert cands[0]["release"] == "Sep2031" and cands[0]["text"] == "new" and cands[0]["pinned_by_id"]
    _, cands = lane.pinned_candidates(None, f"What did {A} say in Jun2031?", find_req=fake_find_req)
    assert cands[0]["release"] == "Jun2031" and cands[0]["text"] == "placeholder body"


@pytest.fixture()
def synth_lane(monkeypatch):
    from core.src.web.routes import query

    seen = {}

    class LLM:
        model = "m"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            seen["context"] = prompt
            return f"answer citing {A}"

    sira = [{"req_id": "VZ_REQ_PLANA_0100", "mno": "mno-w", "release": "Sep2031", "text": "cites the named one"},
            {"req_id": A, "mno": "mno-w", "release": "Sep2031", "text": "sira copy"},
            {"req_id": A, "mno": "mno-w", "release": "Mar2031", "text": "older copy"}]

    async def store(*a, **k):
        return {"results": [dict(r) for r in sira]}

    async def snap():
        return {}

    async def say(msg):
        pass

    monkeypatch.setattr(query, "_build_llm_from_env_or_default", lambda **k: LLM())
    monkeypatch.setattr(playground, "_call_store_query", store)
    monkeypatch.setattr(playground, "_snapshot_sira_lane_config", snap)
    monkeypatch.setattr(req_tree, "find_req", fake_find_req)

    def run(question):
        return asyncio.run(playground._run_select_synth_lane(question, say, 0.0))

    return run, seen


def test_switch_on_puts_the_named_requirement_first(synth_lane, monkeypatch):
    run, seen = synth_lane
    monkeypatch.setenv("NORA_PIN_NAMED_IDS", "on")
    out = run(f"What does {A} require?")
    ctx = seen["context"]
    assert ctx.index(f"req_id: {A}") < ctx.index("req_id: VZ_REQ_PLANA_0100")
    assert "new body" in ctx and "sira copy" not in ctx        # the store's text replaces SIRA's same-release copy
    assert "older copy" in ctx                                 # another release's copy stays
    lc = out["lane_config"]
    assert lc["named_ids"] == [A] and lc["named_pinned"] == [A] and lc["named_in_sira"] == [A]
    assert "new body" not in json.dumps(lc)


def test_switch_off_leaves_the_context_to_bm25(synth_lane, monkeypatch):
    run, seen = synth_lane
    monkeypatch.delenv("NORA_PIN_NAMED_IDS", raising=False)
    out = run(f"What does {A} require?")
    assert "sira copy" in seen["context"] and "new body" not in seen["context"]
    assert "named_ids" not in out["lane_config"]


def test_a_store_failure_never_breaks_the_answer(synth_lane, monkeypatch):
    run, seen = synth_lane
    monkeypatch.setenv("NORA_PIN_NAMED_IDS", "on")

    def boom(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(req_tree, "find_req", boom)
    out = run(f"What does {A} require?")
    assert "sira copy" in seen["context"] and out["result"]["answer"].startswith("answer")
