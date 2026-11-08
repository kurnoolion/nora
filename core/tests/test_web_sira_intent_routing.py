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

PLAIN = "What is the retry timer?"          # a fact question by the rules


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


# ── history_diff_applies ──────────────────────────────────────────────

ON = ReleaseDiffConfig(enabled=True)
OFF = ReleaseDiffConfig(enabled=False)


def test_reads_the_passed_intent_not_the_question_text(monkeypatch):
    monkeypatch.setattr(history_lane, "open_history_store", lambda *a, **k: FakeHistory())
    # the model says change for wording the rules miss, and fact for wording they catch
    passed = _qi(PLAIN, Intent.CHANGE)
    assert history_lane.history_diff_applies(PLAIN, config=ON, intent=passed) is passed
    assert history_lane.history_diff_applies(QUESTION, config=ON,
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
    with pytest.raises(PipelineError):
        history_lane.history_diff_applies(QUESTION, config=ON, intent=change, strict=True)


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


def test_other_questions_go_to_sira(world):
    lanes, _ = _ask(world, PLAIN)
    assert lanes == ["sira"] and world["sira"] == [PLAIN]
