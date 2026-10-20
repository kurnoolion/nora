"""The /v1/systemone HTTP provider (strand jev-experimentation, phase 3).

These tests pin the wire shape as **verified** against the reference
server's own request and response models — `kev/api.py` at jaredpalmer/kev
`1f04cfa`. They were first written against a guessed schema that got four
things wrong (`questions` as a list, `question` for `instructions`,
`options` for `criteria`, and a generic `value` key), so each of those four
now has a test that would fail if the guess came back."""

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
    DecisionQuestion("is_fact", "noul", "Is it a fact lookup?"),
]

_EMPTY = '{"answers": {}}'


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

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
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


class TestEndpointResolution:
    """Both base_url spellings are in circulation: an OpenAI-compatible
    base_url conventionally carries `/v1`, while the reference server's own
    example passes the bare origin. Posting to the wrong one is a 404 that
    looks like an outage."""

    def test_base_url_with_v1(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert cap["url"] == "http://host:8008/v1/systemone"

    def test_bare_origin_gets_v1_added(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert cap["url"] == "http://host:8008/v1/systemone"

    def test_method_is_post(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert cap["method"] == "POST"


class TestRequestShape:
    def test_questions_are_an_object_keyed_by_id(self, monkeypatch):
        """Not a list. A list is a 422 — the server's `questions` field is
        `dict[str, Question]`."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("the query", _QUESTIONS)
        asked = cap["payload"]["questions"]
        assert isinstance(asked, dict)
        assert sorted(asked) == ["is_fact", "query_type"]

    def test_one_request_carries_every_question(self, monkeypatch):
        """Parallel questions against one shared state is the mechanism
        the format exists for."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("the query", _QUESTIONS)
        assert cap["payload"]["state"] == "the query"
        assert len(cap["payload"]["questions"]) == 2
        assert p.call_count == 1

    def test_question_text_is_sent_as_instructions(self, monkeypatch):
        """`question` is a 422; the field is `instructions`."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        entry = cap["payload"]["questions"]["query_type"]
        assert entry["instructions"] == "Shape?"
        assert "question" not in entry

    def test_choice_options_are_a_criteria_object(self, monkeypatch):
        """`options` is a 422; the field is `criteria`, and for a choice it
        is an object whose values are optional per-option descriptions.
        None presents the option by name alone."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        entry = cap["payload"]["questions"]["query_type"]
        assert entry["type"] == "choice"
        assert entry["criteria"] == {"fact": None, "general": None}
        assert "options" not in entry

    def test_criteria_preserves_option_order(self, monkeypatch):
        """Option order can change an answer, so the order the caller
        chose has to survive serialisation."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        entry = cap["payload"]["questions"]["query_type"]
        assert list(entry["criteria"]) == ["fact", "general"]

    def test_noul_sends_no_criteria(self, monkeypatch):
        """A noul's two options are implicit."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        entry = cap["payload"]["questions"]["is_fact"]
        assert entry["type"] == "noul"
        assert "criteria" not in entry

    def test_score_criteria_is_an_ordered_list(self, monkeypatch):
        """A score's criteria is a list of ordered level descriptions, not
        an object — the one place the two question types differ."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        question = DecisionQuestion(
            "severity", "score", "How severe?", ("low", "medium", "high")
        )
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", [question])
        entry = cap["payload"]["questions"]["severity"]
        assert entry["criteria"] == ["low", "medium", "high"]

    def test_empty_model_is_omitted(self, monkeypatch):
        """Empty means "the server's own default", which is what a
        single-model deployment wants — sending "" would override it."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert "model" not in cap["payload"]

    def test_model_is_sent_when_set(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1", model="kev-4b")
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert cap["payload"]["model"] == "kev-4b"

    def test_api_key_sent_only_when_set(self, monkeypatch):
        bare = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY) as cap:
            bare.decide("q", _QUESTIONS)
        assert not any(k.lower() == "authorization" for k in cap["headers"])

        keyed = SystemOneDecisionProvider("http://host:8008/v1", api_key="k")
        with _served(monkeypatch, _EMPTY) as cap:
            keyed.decide("q", _QUESTIONS)
        assert cap["headers"]["Authorization"] == "Bearer k"

    def test_timeout_is_passed(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1", timeout=7)
        with _served(monkeypatch, _EMPTY) as cap:
            p.decide("q", _QUESTIONS)
        assert cap["timeout"] == 7


class TestResponseParsing:
    def test_answers_are_an_object_keyed_by_id(self, monkeypatch):
        """Not a list, and each answer keeps its value under a field named
        after its type rather than a generic `value`."""
        body = json.dumps({"answers": {
            "query_type": {
                "type": "choice",
                "choice": "fact",
                "confidence": 0.91,
                "probabilities": {"fact": 0.91, "general": 0.09},
            },
            "is_fact": {"type": "noul", "noul": 0.87},
        }})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            answers = p.decide("q", _QUESTIONS)
        by_id = {a.question_id: a for a in answers}
        assert sorted(by_id) == ["is_fact", "query_type"]
        assert by_id["query_type"].value == "fact"
        assert by_id["query_type"].confidence == 0.91

    def test_a_noul_value_is_the_probability_of_yes(self, monkeypatch):
        """And it carries no confidence of its own — the value already is
        one, which is why `confidence_is_calibrated` cannot read a
        noul-only response."""
        body = json.dumps(
            {"answers": {"is_fact": {"type": "noul", "noul": 0.87}}}
        )
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            answers = p.decide("q", [_QUESTIONS[1]])
        assert answers[0].value == 0.87
        assert answers[0].confidence == 0.0

    def test_a_score_value_is_the_expected_level(self, monkeypatch):
        body = json.dumps({"answers": {"severity": {
            "type": "score", "score": 1.4,
            "legend": {"0": "low", "1": "medium", "2": "high"},
            "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
            "confidence": 0.3,
        }}})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        question = DecisionQuestion(
            "severity", "score", "How severe?", ("low", "medium", "high")
        )
        with _served(monkeypatch, body):
            answers = p.decide("q", [question])
        assert answers[0].value == 1.4
        assert answers[0].confidence == 0.3

    def test_client_latency_recorded(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY):
            p.decide("q", _QUESTIONS)
        assert p.last_latency_ms >= 0.0

    def test_server_latency_recorded_separately(self, monkeypatch):
        """The difference between the two is network, which is what tells
        a slow model from a slow hop."""
        body = json.dumps({"answers": {}, "latency_ms": 94.2})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            p.decide("q", _QUESTIONS)
        assert p.last_server_latency_ms == 94.2

    def test_server_latency_defaults_to_zero(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, _EMPTY):
            p.decide("q", _QUESTIONS)
        assert p.last_server_latency_ms == 0.0


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
        """A schema mismatch must say exactly what to correct rather than
        guessing an alias."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"results": [], "usage": {}}'):
            with pytest.raises(DecisionError, match="results.*usage|usage"):
                p.decide("q", _QUESTIONS)

    def test_answers_as_a_list_is_diagnosed(self, monkeypatch):
        """The exact shape the first draft of this client assumed. If a
        future server sends it, the message says so rather than crashing
        on an attribute."""
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, '{"answers": [{"id": "query_type"}]}'):
            with pytest.raises(DecisionError, match="keyed by question id"):
                p.decide("q", _QUESTIONS)

    def test_unknown_answer_type_names_the_types_it_knows(self, monkeypatch):
        body = json.dumps(
            {"answers": {"query_type": {"type": "bool", "value": True}}}
        )
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            with pytest.raises(DecisionError, match="unknown type 'bool'"):
                p.decide("q", _QUESTIONS)

    def test_answer_missing_its_value_field_names_its_keys(self, monkeypatch):
        body = json.dumps({"answers": {
            "query_type": {"type": "choice", "probabilities": {"fact": 1.0}}
        }})
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, body):
            with pytest.raises(DecisionError, match="probabilities"):
                p.decide("q", _QUESTIONS)

    def test_latency_recorded_even_on_failure(self, monkeypatch):
        p = SystemOneDecisionProvider("http://host:8008/v1")
        with _served(monkeypatch, "", error=OSError("boom")):
            with pytest.raises(DecisionError):
                p.decide("q", _QUESTIONS)
        assert p.last_latency_ms >= 0.0
