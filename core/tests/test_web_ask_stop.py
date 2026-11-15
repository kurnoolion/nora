"""Ask page, SIRA select-synth lane: early candidates and Stop (strand
ask-stream-stop, issue #30)."""
import asyncio
import threading

import pytest

from core.src.llm.openai_provider import CANCEL, LLMCancelled
from core.src.web.routes import history_lane, playground, query
from core.tests.test_web_history_lane import FakeFeedback, _events

RID = "VZ_REQ_PLANA_0001"


@pytest.fixture()
def lane(monkeypatch):
    seen = {"calls": 0, "partials": [], "stop_in": None}

    class LLM:
        model = "m"

        def complete(self, prompt, system="", temperature=0.0, max_tokens=0):
            seen["calls"] += 1
            seen["cancel_in_thread"] = CANCEL.get()
            if seen["stop_in"] == "model":          # Stop pressed while the model writes
                CANCEL.get().set()
                raise LLMCancelled("LLM call cancelled")
            return f"answer citing {RID}"

    async def store(*a, **k):
        seen["store_calls"] = seen.get("store_calls", 0) + 1
        return {"results": [{"req_id": RID, "mno": "mno-w", "release": "Sep2031",
                             "text": "corpus placeholder", "rank": 1, "bm25_score": 1.0, "rerank_score": None},
                            {"req_id": "VZ_REQ_PLANA_0002", "mno": "mno-w", "release": "Sep2031",
                             "text": "corpus placeholder", "rank": 1, "bm25_score": 1.0, "rerank_score": None}]}

    async def snap():
        return {}

    monkeypatch.setattr(query, "_build_llm_from_env_or_default", lambda **k: LLM())
    monkeypatch.setattr(playground, "_call_store_query", store)
    monkeypatch.setattr(playground, "_snapshot_sira_lane_config", snap)

    def run(cancel=None):
        async def say(msg):
            pass

        async def partial(p):
            seen["partials"].append(p)
            if seen["stop_in"] == "search":         # Stop pressed once candidates show
                cancel.set()
        return asyncio.run(playground._run_select_synth_lane(
            "q", say, 0.0, cancel=cancel, emit_partial=partial))

    return run, seen


def test_candidates_go_out_before_the_answer_labelled_and_without_corpus_text(lane):
    run, seen = lane
    out = run(threading.Event())
    (p,) = seen["partials"]
    assert p["lane"] == "sira" and p["count"] == 2
    assert "Searched: 2 candidates" in p["html"] and "the answer cites from these" in p["html"]
    assert RID in p["html"] and 'class="card mb-2' in p["html"]       # the tab's tiles
    assert out["result"]["answer"].startswith(f"answer citing {RID}")
    assert [r["cited"] for r in out["sira_results"]] == [True, False]
    assert "stopped" not in out["lane_config"]


def test_the_model_call_sees_the_cancel_event(lane):
    run, seen = lane
    ev = threading.Event()
    run(ev)
    assert seen["cancel_in_thread"] is ev


def test_stop_during_the_model_call_stores_a_stopped_answer(lane):
    run, seen = lane
    seen["stop_in"] = "model"
    out = run(threading.Event())
    assert out["result"]["answer"] == playground._STOPPED_NOTE
    assert out["lane_config"]["stopped"] is True
    assert out["synth_error"] is None and out["cited_ids"] == []
    assert len(out["sira_results"]) == 2                     # the card keeps the candidates


def test_stop_before_the_model_call_never_calls_it(lane):
    run, seen = lane
    seen["stop_in"] = "search"
    out = run(threading.Event())
    assert seen["calls"] == 0
    assert out["lane_config"]["stopped"] is True


# --- the stream route, team-mode gate on --------------------------------------

@pytest.fixture()
def client(lane, monkeypatch):
    from fastapi.testclient import TestClient
    from core.src.web import team_mode
    from core.src.web.app import app

    monkeypatch.setattr(team_mode, "TEAM_MODE", True)       # gate on, no admin cookie
    monkeypatch.setattr(team_mode, "ADMIN_TOKEN", "sek")
    monkeypatch.setattr(playground, "_SELECT_SYNTH_ENABLED", True)
    monkeypatch.setattr(playground, "_SYNTH_MODE", "select-synth")   # the two agree in real config
    monkeypatch.delenv("NORA_ID_LOOKUP", raising=False)
    app.state.feedback_store = FakeFeedback()
    return TestClient(app), lane[1], app.state.feedback_store


def _stream(c):
    r = c.post("/api/test/ask-stream",
               data={"question": "what is the timer value", "lanes": ["sira"], "section": "merged"})
    assert r.status_code == 200
    return _events(r.text)


def test_stream_sends_a_run_id_and_the_candidates_before_done(client):
    c, seen, store = client
    ev = _stream(c)
    names = [n for n, _ in ev]
    assert ev[0][1]["lanes"] == ["sira"] and ev[0][1]["run_id"]
    assert names.index("partial") < names.index("done")
    assert history_lane.cancel_event(ev[0][1]["run_id"]) is None    # forgotten once done


def test_stopped_ask_renders_the_note_and_marks_the_row(client):
    c, seen, store = client
    seen["stop_in"] = "model"
    ev = _stream(c)
    html = [d for n, d in ev if n == "done"][0]["html"]
    assert "Stopped before the answer" in html
    assert 'nav-link py-1 active" type="button" role="tab"\n                  data-bs-toggle="tab" data-bs-target="#sira-tabs-1-cands"' in html
    row = store.rows[0]
    assert row["answer"] == playground._STOPPED_NOTE and row["lane_config"]["stopped"] is True


def test_ask_cancel_passes_the_team_gate(client):
    c, _, _ = client
    run_id = history_lane.new_run_id()
    r = c.post("/api/test/ask-cancel", data={"run_id": run_id})
    assert r.status_code == 200 and history_lane.cancel_event(run_id).is_set()
    history_lane.forget_run(run_id)


def test_history_pane_shows_a_stopped_row_as_stopped(client):
    import json
    c, seen, store = client
    seen["stop_in"] = "model"
    _stream(c)
    row = dict(store.rows[0], id=1, lane_config=json.dumps(store.rows[0]["lane_config"]))

    async def get_row(row_id):
        return row if row_id == 1 else None
    store.get_row = get_row
    html = c.get("/api/ask/s/1").text
    assert "Stopped before the answer" in html


def test_stop_after_the_answer_arrived_keeps_the_answer(lane, monkeypatch):
    run, seen = lane
    ev = threading.Event()
    real = playground._select_synth_synthesize

    def answer_then_stop(*a, **k):
        out = real(*a, **k)
        ev.set()                                            # Stop lands just too late
        return out
    monkeypatch.setattr(playground, "_select_synth_synthesize", answer_then_stop)
    out = run(ev)
    assert out["result"]["answer"].startswith(f"answer citing {RID}")
    assert "stopped" not in out["lane_config"]


def test_the_card_has_answer_cited_and_candidates_tabs(client):
    c, seen, store = client
    html = [d for n, d in _stream(c) if n == "done"][0]["html"]
    for tab in (">Answer</button>", ">Cited by LLM (1)</button>", ">Search candidates (2)</button>"):
        assert tab in html, tab
    assert 'data-bs-target="#sira-tabs-1-answer"' in html
    assert "SIRA retrieval (top" not in html                  # moved out of engineering details


def test_the_default_rerank_pin_card_keeps_its_citation_list_and_no_tabs(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setattr(playground, "_SELECT_SYNTH_ENABLED", False)
    monkeypatch.setattr(playground, "_SYNTH_MODE", "rerank-pin")

    async def rerank_pin(question, request, **kw):
        return {"result": {"answer": f"answer citing {RID}", "llm_citations": [{"req_id": RID}]},
                "sira_result": {}, "sira_results": [{"req_id": RID, "mno": "mno-w", "release": "Sep2031",
                                                     "text": "corpus placeholder", "rank": 1, "bm25_score": 1.0,
                                                     "rerank_score": None, "pinned": True}],
                "max_rerank_score": 0, "pinned_count": 0, "synth_error": None, "elapsed_ms": 1,
                "retrieved_ids": [RID], "reranked_ids": None, "cited_ids": [RID], "lane_config": {}}
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", rerank_pin)
    html = [d for n, d in _stream(c) if n == "done"][0]["html"]
    assert "nav-tabs" not in html and ">Cited by LLM (" not in html
    assert "<strong>Cited by LLM</strong>" in html


# --- alongside the ID-lookup and History lanes: no SIRA candidates, no Stop ---

def _sira_events(ev):
    return [d for n, d in ev if n == "partial" and d.get("lane") == "sira"]


def test_an_id_lookup_ask_gets_no_candidates_or_stop(client, monkeypatch):
    c, seen, _ = client
    monkeypatch.setenv("NORA_ID_LOOKUP", "on")
    r = c.post("/api/test/ask-stream", data={"question": RID, "lanes": ["sira"], "section": "merged"})
    ev = _events(r.text)
    assert ev[0][1] == {"lanes": ["id_lookup"], "run_id": ""}     # no run id: the page shows no Stop
    assert _sira_events(ev) == [] and "store_calls" not in seen
    assert [n for n, _ in ev].count("done") == 1


def test_a_history_ask_keeps_its_cancel_and_gets_no_candidates(client, monkeypatch):
    c, seen, _ = client
    monkeypatch.setattr(history_lane, "history_diff_applies", lambda *a, **k: object())

    async def history_run(question, intent, **kw):
        return {"error": "stubbed history"}
    monkeypatch.setattr(history_lane, "run_history_lane", history_run)
    ev = _stream(c)
    assert ev[0][1]["lanes"] == ["history"] and ev[0][1]["run_id"]   # History's Cancel
    assert _sira_events(ev) == [] and "store_calls" not in seen
    assert [n for n, _ in ev].count("done") == 1
