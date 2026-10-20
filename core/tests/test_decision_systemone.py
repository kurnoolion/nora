"""The /v1/systemone HTTP provider (strand jev-experimentation, phase 3).

The wire schema is unverified against a live server, so these tests pin
the shape this client sends and the diagnostics it produces when a real
server disagrees."""

from __future__ import annotations

import json
from contextlib import contextmanager
from io import BytesIO

import pytest

from core.src.decision.base import (
    DecisionError,
    DecisionProvider,
    DecisionQuestion,
)
from core.src.decision.systemone_provider import SystemOneDecisionProvider

_QUESTIONS = [
    DecisionQuestion("query_type", "choice", "Shape?", ("fact", "general")),
    DecisionQuestion("is_fact", "bool", "Is it a fact lookup?"),
]


@contextmanager
def _served(monkeypatch, body, error=None):
    """Replace urlopen; record the request the client actually built."""
    captured = {}

    class _Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        if error is not None:
            raise error
        return _Response(body.encode("utf-8"))

    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen
    )
    yield captured


class TestConstruction:
    def test_satisfies_the_protocol(self):
        p = SystemOneDecisionProvider("http://host:8008/v1", model="kev-4b")
        assert isinstance(p, DecisionProvider)
        assert p.model == "kev-4b"

    def test_no_base_url_is_refused(self):
        with pytest.raises(DecisionError, match="base_url"):
            SystemOneDecisionProvider("")

    def test_trailing_slash_normalised(self):
        p = SystemOneDecisionProvider("http://host:8008/v1/")
        assert p.base_url == "http://host:8008/v1"


class TestRequestShape:
    def test_posts_to_systemone(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("q", _QUESTIONS)
        assert cap["url"] == "http://host:8008/v1/systemone"
        assert cap["method"] == "POST"

    def test_one_request_carries_every_question(self, monkeypatch):
        """Parallel questions against one shared state is the mechanism
        the format exists for."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("the query", _QUESTIONS)
        assert cap["payload"]["state"] == "the query"
        assert [q["id"] for q in cap["payload"]["questions"]] == [
            "query_type", "is_fact"
        ]
        assert p.call_count == 1

    def test_choice_carries_options_and_bool_does_not(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("q", _QUESTIONS)
        choice, boolean = cap["payload"]["questions"]
        assert choice["options"] == ["fact", "general"]
        assert "options" not in boolean

    def test_empty_model_is_omitted(self, monkeypatch):
        """Empty means "the server's own default", which is what a
        single-model deployment wants — sending "" would override it."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("q", _QUESTIONS)
        assert "model" not in cap["payload"]

    def test_model_is_sent_when_set(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1", model="kev-4b")
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("q", _QUESTIONS)
        assert cap["payload"]["model"] == "kev-4b"

    def test_api_key_sent_only_when_set(self, monkeypatch):
        bare = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}') as cap:
            bare.decide("q", _QUESTIONS)
        assert not any(k.lower() == "authorization" for k in cap["headers"])

        keyed = SystemOneDecisionProvider("http://host:8008/v1", api_key="k")
        with _served(monkeypatch, '{"answers": []}') as cap:
            keyed.decide("q", _QUESTIONS)
        assert cap["headers"]["Authorization"] == "Bearer k"

    def test_timeout_is_passed(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1", timeout=7)
        with _served(monkeypatch, '{"answers": []}') as cap:
            p.decide("q", _QUESTIONS)
        assert cap["timeout"] == 7


class TestResponseParsing:
    def test_answers_parsed(self, monkeypatch):
        body = json.dumps({"answers": [
            {"id": "query_type", "value": "fact", "confidence": 0.91},
            {"id": "is_fact", "value": True},
        ]})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            answers = p.decide("q", _QUESTIONS)
        assert [a.question_id for a in answers] == ["query_type", "is_fact"]
        assert answers[0].value == "fact"
        assert answers[0].confidence == 0.91
        assert answers[1].value is True
        assert answers[1].confidence == 0.0

    def test_latency_recorded(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": []}'):
            p.decide("q", _QUESTIONS)
        assert p.last_latency_ms >= 0.0


class TestFailureDiagnostics:
    def test_transport_failure_raises_decision_error(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, "", error=OSError("connection refused")):
            with pytest.raises(DecisionError, match="connection refused"):
                p.decide("q", _QUESTIONS)

    def test_non_json_raises(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, "<html>502</html>"):
            with pytest.raises(DecisionError, match="not JSON"):
                p.decide("q", _QUESTIONS)

    def test_missing_answers_key_names_what_the_server_sent(self, monkeypatch):
        """The wire schema is unverified, so a mismatch must say exactly
        what to correct rather than guessing an alias."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"results": [], "usage": {}}'):
            with pytest.raises(DecisionError, match="results.*usage|usage"):
                p.decide("q", _QUESTIONS)

    def test_answer_missing_value_names_its_keys(self, monkeypatch):
        body = json.dumps({"answers": [{"id": "query_type", "choice": "fact"}]})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            with pytest.raises(DecisionError, match="choice"):
                p.decide("q", _QUESTIONS)

    def test_latency_recorded_even_on_failure(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, "", error=OSError("boom")):
            with pytest.raises(DecisionError):
                p.decide("q", _QUESTIONS)
        assert p.last_latency_ms >= 0.0
