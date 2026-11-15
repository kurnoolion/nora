"""Change routing on the Ask page (strand sira-intent, design §4, D-DRAFT-3/5).

Covers:
- `sira_intent.classify`: the knob's analyzer, asked once; rules trace by
  default, the decision trace on a decision arm.
- `history_lane.history_diff_applies` reads the intent nora-web passed;
  strict mode raises RHD-E003 for a missing store; a disabled lane is
  never an error.
- `run_history_lane` names the served releases on RHD-E001.
- The ask-stream route: classify once, a model `change` goes to History,
  strict on with no store is an error (SIRA not run), strict off keeps
  today's SIRA answer, the lane disabled keeps SIRA.

Fake stores and providers; no network, no corpus text.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")

from core.src.pipeline.error_codes import PipelineError  # noqa: E402
from core.src.query.release_diff import ReleaseDiffConfig  # noqa: E402
from core.src.query.schema import Intent, QueryIntent  # noqa: E402
from core.src.web.routes import history_lane, playground, sira_intent  # noqa: E402
from core.tests.test_web_history_lane import (  # noqa: E402
    QUESTION, EchoProvider, FakeFeedback, FakeHistory, _events,
)

PLAIN = "What is the retry timer?"          # not a change question; the rules say other


def _qi(text, intent):
    return QueryIntent(raw_query=text, intent=intent)


@pytest.fixture(autouse=True)
def _fresh_analyzer(monkeypatch):
    monkeypatch.delenv("NORA_QUERY_ANALYZER", raising=False)
    monkeypatch.delenv("NORA_SIRA_INTENT_PROMPTS", raising=False)
    sira_intent.analyzer.cache_clear()
    yield
    sira_intent.analyzer.cache_clear()


# ── classify ──────────────────────────────────────────────────────────

def test_rules_by_default_with_a_rules_trace():
    qi, trace = sira_intent.classify(QUESTION)
    assert qi.intent is Intent.CHANGE
    assert trace == {"intent": {"source": "rules", "confidence": None}}
    assert sira_intent.classify(PLAIN)[0].intent is not Intent.CHANGE


def test_decision_arm_reports_its_trace(monkeypatch):
    monkeypatch.setenv("NORA_QUERY_ANALYZER", "decision")
    monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
    qi, trace = sira_intent.classify(PLAIN)
    assert set(trace) == {"intent"} and trace["intent"]["source"] in {"model", "rules"}


@pytest.mark.parametrize("value, on", [(None, False), ("off", False), ("on", True), ("TRUE", True)])
def test_prompts_switch(monkeypatch, value, on):
    if value is not None:
        monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", value)
    assert sira_intent.prompts_enabled() is on


@pytest.mark.parametrize("prompts, arm, on", [
    (None, None, False), ("off", "rules", False),
    ("on", None, True), (None, "decision", True)])
def test_intent_routing_switch(monkeypatch, prompts, arm, on):
    if prompts is not None:
        monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", prompts)
    if arm is not None:
        monkeypatch.setenv("NORA_QUERY_ANALYZER", arm)
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
    assert sira_intent.intent_routing_enabled() is on


# ── history_diff_applies ──────────────────────────────────────────────

ON = ReleaseDiffConfig(enabled=True)
OFF = ReleaseDiffConfig(enabled=False)


def test_reads_the_passed_intent_not_the_question_text(monkeypatch):
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: FakeHistory())
    # the model says change for wording the rules miss, and fact for wording they catch
    passed = _qi(PLAIN, Intent.CHANGE)
    assert history_lane.history_diff_applies(PLAIN, config=ON, intent=passed,
                                             intent_routing=True) is passed
    assert history_lane.history_diff_applies(QUESTION, config=ON, intent_routing=True,
                                             intent=_qi(QUESTION, Intent.FACT)) is None


def test_strict_raises_for_a_missing_or_broken_store(monkeypatch):
    change = _qi(QUESTION, Intent.CHANGE)
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: None)
    assert history_lane.history_diff_applies(QUESTION, config=ON, intent=change) is None
    with pytest.raises(PipelineError) as e:
        history_lane.history_diff_applies(QUESTION, config=ON, intent=change, strict=True)
    assert e.value.code == "RHD-E003"

    def broken(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(history_lane, "open_history_store", broken)
    assert history_lane.history_diff_applies(QUESTION, config=ON, intent=change) is None
    with pytest.raises(PipelineError) as e:
        history_lane.history_diff_applies(QUESTION, config=ON, intent=change, strict=True)
    # A configured store that fails to open is not "not configured".
    assert e.value.code == "RHD-E005" and "OSError" in e.value.message


def test_a_disabled_lane_is_never_an_error(monkeypatch):
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: None)
    assert history_lane.history_diff_applies(
        QUESTION, config=OFF, intent=_qi(QUESTION, Intent.CHANGE), strict=True) is None


def test_single_release_error_names_what_history_has():
    class OneRelease(FakeHistory):
        def releases(self, mno):
            return ["Jan2031"]

    out = asyncio.run(history_lane.run_history_lane(
        QUESTION, history_lane.classify(QUESTION), provider=EchoProvider(), run_id="x",
        config=ON, history=OneRelease()))
    assert out["error_code"] == "RHD-E001"
    assert "History has Jan2031 for mno-w" in out["error"]


# ── the route ─────────────────────────────────────────────────────────

@pytest.fixture()
def world(monkeypatch):
    from fastapi.testclient import TestClient
    from core.src.web.app import app

    state = {"store": FakeHistory(), "cfg": ON, "sira": [], "classified": []}
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: state["store"])
    monkeypatch.setattr(history_lane, "ReleaseDiffConfig",
                        type("Cfg", (), {"from_env": staticmethod(lambda env=None: state["cfg"])}))
    monkeypatch.setattr(playground, "_history_provider", lambda *a, **k: EchoProvider())

    async def fake_sira(question, request, **kw):
        state["sira"].append(question)
        state["sira_intent"] = kw.get("intent")
        return {"error": "stubbed"}

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", fake_sira)
    real = sira_intent.classify

    def counting(q):
        state["classified"].append(q)
        return real(q)

    monkeypatch.setattr(sira_intent, "classify", counting)
    app.state.feedback_store = FakeFeedback()
    state["client"] = TestClient(app)
    return state


def _ask(w, question):
    r = w["client"].post("/api/test/ask-stream",
                         data={"question": question, "lanes": ["sira"], "section": "merged"})
    assert r.status_code == 200
    ev = _events(r.text)
    return ev[0][1]["lanes"], [d for n, d in ev if n == "done"][0]["html"]


def test_classified_once_and_a_change_goes_to_history(world):
    lanes, _ = _ask(world, QUESTION)
    assert lanes == ["history"] and world["classified"] == [QUESTION] and world["sira"] == []


def test_a_model_change_label_routes_to_history(world, monkeypatch):
    monkeypatch.setenv("NORA_QUERY_ANALYZER", "decision")
    monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
    monkeypatch.setattr(sira_intent, "classify",
                        lambda q: (_qi(q, Intent.CHANGE), {"intent": {"source": "model", "confidence": 0.9}}))
    lanes, _ = _ask(world, PLAIN)
    assert lanes == ["history"] and world["sira"] == []


def test_strict_with_no_store_is_an_error_not_a_sira_answer(world, monkeypatch):
    monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", "on")
    world["store"] = None
    lanes, html = _ask(world, QUESTION)
    assert lanes == ["history"] and world["sira"] == []
    assert "HISTORY error" in html and "RHD-E003" in html and "single release" in html


def test_strict_off_with_no_store_keeps_todays_sira_answer(world):
    world["store"] = None
    lanes, _ = _ask(world, QUESTION)
    assert lanes == ["sira"] and world["sira"] == [QUESTION]


def test_lane_disabled_goes_to_sira_even_when_strict(world, monkeypatch):
    monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", "on")
    world["cfg"] = OFF
    lanes, _ = _ask(world, QUESTION)
    assert lanes == ["sira"] and world["sira"] == [QUESTION]
    assert world["sira_intent"] is Intent.CHANGE      # SIRA answers with the change prompt


VERDICT = "Is it correct that the IMS registration requirement changed in Sep 2031?"


def test_switches_off_route_like_main(world):
    # main's rule sends a verdict ask about a change to History; the rules
    # intent (compliance) must not change that while every switch is off
    lanes, _ = _ask(world, VERDICT)
    assert lanes == ["history"] and world["sira"] == []


def test_prompts_on_routes_by_intent(world, monkeypatch):
    monkeypatch.setenv("NORA_SIRA_INTENT_PROMPTS", "on")
    lanes, _ = _ask(world, VERDICT)
    assert lanes == ["sira"] and world["sira"] == [VERDICT]


def test_other_questions_go_to_sira(world):
    lanes, _ = _ask(world, PLAIN)
    assert lanes == ["sira"] and world["sira"] == [PLAIN]
    assert world["sira_intent"] is Intent.OTHER


# ── badge and lane_config (design §7) ─────────────────────────────────

def _sira_ok(question, request, **kw):
    async def run():
        return {"result": {"answer": "an answer"}, "sira_result": {}, "sira_results": [],
                "max_rerank_score": 0, "pinned_count": 0, "synth_error": None,
                "elapsed_ms": 1, "retrieved_ids": [], "reranked_ids": None,
                "cited_ids": [], "lane_config": {"stack": "s"}}
    return run()


def _rows(w):
    from core.src.web.app import app
    return app.state.feedback_store.rows


@pytest.mark.parametrize("team", [False, True])
def test_sira_row_records_the_intent_and_the_card_shows_the_badge(world, monkeypatch, team):
    if team:
        import core.src.web.team_mode as tm
        monkeypatch.setattr(tm, "TEAM_MODE", True)
        monkeypatch.setattr(tm, "ADMIN_TOKEN", "sek")
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    lanes, html = _ask(world, PLAIN)
    assert lanes == ["sira"]
    lc = _rows(world)[0]["lane_config"]
    assert lc["intent"] == "other" and lc["intent_source"] == "rules"
    assert lc["intent_confidence"] is None and lc["intent_analyzer"] == "rules"
    assert lc["stack"] == "s"                                  # the lane's own snapshot kept
    assert 'title="Answer intent">other</span>' in html


def test_a_model_decision_is_recorded_with_its_confidence(world, monkeypatch):
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    monkeypatch.setattr(sira_intent, "classify",
                        lambda q: (_qi(q, Intent.LIST), {"intent": {"source": "model", "confidence": 0.71}}))
    _, html = _ask(world, PLAIN)
    lc = _rows(world)[0]["lane_config"]
    assert (lc["intent"], lc["intent_source"], lc["intent_confidence"]) == ("list", "model", 0.71)
    assert 'title="Answer intent">list</span>' in html


def test_history_row_records_the_change_intent(world):
    lanes, html = _ask(world, QUESTION)
    assert lanes == ["history"]
    lc = _rows(world)[0]["lane_config"]
    assert lc["lane"] == "history" and lc["intent"] == "change" and lc["intent_source"] == "rules"
    assert 'title="Answer intent">change</span>' in html


def test_badge_rules():
    assert sira_intent.intent_badge("id_lookup", {"intent": "other"}) == "lookup"
    assert sira_intent.intent_badge("sira", {"intent": "fact"}) == "fact"
    assert sira_intent.intent_badge("nora", {}) == "" and sira_intent.intent_badge("sira", None) == ""


def test_the_shared_page_shows_the_stored_badge(world):
    import json as _json
    from core.src.web.app import app

    class Store:
        async def get_row(self, row_id):
            return {"id": 1, "lane": "sira", "question": "q", "answer": "a", "user_name": "u",
                    "timestamp": "t", "lane_config": _json.dumps({"intent": "compare"}),
                    "cited_ids": "[]", "retrieved_ids": "[]", "citations_json": "[]"}

    app.state.feedback_store = Store()
    r = world["client"].get("/api/ask/s/1")
    assert r.status_code == 200 and 'title="Answer intent">compare</span>' in r.text


# ── scope badges: the cells SIRA searched, beside the intent badge ────

def test_the_card_shows_the_searched_cells_and_the_missing_scope(world, monkeypatch):
    def sira_cells(question, request, **kw):
        async def run():
            out = await _sira_ok(question, request, **kw)
            out["sira_result"] = {"mode": "multi-cell", "resolved_cells": ["MNO_A/R2"],
                                  "unresolved": [["MNO_B", "*"], ["MNO_A", "R9"]]}
            return out
        return run()

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", sira_cells)
    _, html = _ask(world, PLAIN)
    assert 'title="Searched (MNO/release)">MNO_A/R2</span>' in html
    assert 'title="Named in the question but not ingested">MNO_B</span>' in html
    assert 'title="Named in the question but not ingested">MNO_A / R9</span>' in html


def test_no_scope_badge_without_cells(world, monkeypatch):
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    _, html = _ask(world, PLAIN)
    assert 'title="Searched (MNO/release)"' not in html


def test_progress_rows_can_be_hidden(world):
    # Bootstrap's d-flex is display:flex !important, which beats the inline
    # display:none and the JS that hides the lanes an ask does not run.
    import re
    html = world["client"].get("/test").text
    for lane in ("nora", "sira", "history"):
        tag = re.search(rf'<div id="progress-row-{lane}"[^>]*>', html).group(0)
        assert "d-flex" not in tag, tag


def test_the_timeline_names_the_expansion_and_answer_models(world, monkeypatch):
    def sira_timed(question, request, **kw):
        async def run():
            out = await _sira_ok(question, request, **kw)
            out["elapsed_ms"] = 1000
            out["sira_result"] = {"timings_ms": {"expand_ms": 600, "search_ms": 10,
                                                 "synth_ms": 300},
                                  "expand_model": "m-expand"}
            out["result"]["llm_model"] = "m-answer"
            return out
        return run()

    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", sira_timed)
    _, html = _ask(world, PLAIN)
    assert 'title="Query expansion model">m-expand</span>' in html
    assert 'title="Answer model">m-answer</span>' in html


def test_share_sits_in_the_header_beside_asked_by(world, monkeypatch):
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    _, html = _ask(world, PLAIN)
    header = html[: html.index('class="row g-3"')]
    assert "askCopyShare(this, '/ask/s/" in header
def test_the_card_links_a_prefilled_issue_when_configured(world, monkeypatch):
    monkeypatch.setenv("NORA_ISSUE_URL", "https://git.example.test/org/repo/issues/new")
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    _, html = _ask(world, PLAIN)
    assert 'href="https://git.example.test/org/repo/issues/new?title=Answer+issue' in html
    header = html[: html.index('class="row g-3"')]
    assert header.count(">Report issue</a>") == 1 == html.count(">Report issue</a>")  # beside Share only
    assert header.index("askCopyShare(") < header.index(">Report issue</a>")
    assert "btn btn-sm btn-danger" in header
    # The share link is path-only; the browser adds its origin (askIssueLink),
    # so the server's view of the host never reaches the issue.
    assert "askIssueLink(this, '/ask/s/" in header
    assert "Link%3A+%2Fask%2Fs%2F" in html and "testserver" not in html


def test_no_issue_link_by_default(world, monkeypatch):
    monkeypatch.delenv("NORA_ISSUE_URL", raising=False)
    monkeypatch.setattr(playground, "_run_sira_lane_for_merged", _sira_ok)
    _, html = _ask(world, PLAIN)
    assert "Report issue" not in html
