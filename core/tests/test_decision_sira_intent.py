"""Tests for the SIRA intent question (strand sira-intent, design §3).

Covers:
- `INTENT_DESCRIPTIONS` glosses every `Intent`; `INTENT_QUESTION` options
  are the enum; `SIRA_QUESTIONS` is that one question.
- The decision analyzer asks only the questions it was built with.
- Confidence routing on intent: above the floor the model, below it the
  rules, no rule match `other`; a provider outage gives the rules.
- `analyze_with_trace` reports per-field source and confidence.
- The knob factory passes `questions` through on both decision arms.
"""

from __future__ import annotations

from core.src.decision.analyzer import (
    CLASSIFICATION_QUESTIONS,
    INTENT_DESCRIPTIONS,
    INTENT_QUESTION,
    SIRA_QUESTIONS,
    ConfidenceRoutedQueryAnalyzer,
    DecisionQueryAnalyzer,
)
from core.src.decision.base import DecisionAnswer, DecisionError
from core.src.query.schema import Intent, QueryType


class _Scripted:
    model = "scripted"

    def __init__(self, values, confidence=1.0):
        self._values = values
        self._confidence = confidence
        self.seen = []

    def decide(self, state, questions):
        self.seen.append([q.question_id for q in questions])
        return [DecisionAnswer(q.question_id, self._values[q.question_id], self._confidence)
                for q in questions if q.question_id in self._values]


class _Down:
    model = "down"

    def decide(self, state, questions):
        raise DecisionError("provider down")


def test_every_intent_has_a_gloss():
    assert set(INTENT_DESCRIPTIONS) == set(Intent)
    assert all(INTENT_DESCRIPTIONS[i].strip() for i in Intent)


def test_intent_question_is_generated_from_the_enum():
    assert INTENT_QUESTION.question_id == "intent"
    assert INTENT_QUESTION.kind == "choice"
    assert INTENT_QUESTION.options == tuple(i.value for i in Intent)
    assert INTENT_QUESTION.descriptions == tuple(INTENT_DESCRIPTIONS[i] for i in Intent)
    assert INTENT_QUESTION.options[-1] == Intent.OTHER.value  # mock's choice fallback is the last option


def test_sira_questions_is_only_intent_and_nora_questions_are_unchanged():
    assert SIRA_QUESTIONS == (INTENT_QUESTION,)
    assert INTENT_QUESTION not in CLASSIFICATION_QUESTIONS


def test_default_analyzer_still_asks_the_nora_questions():
    p = _Scripted({"query_type": "fact", "doc_type_scope": "requirements"})
    DecisionQueryAnalyzer(p).analyze("anything")
    assert p.seen == [["query_type", "doc_type_scope"]]


def test_sira_analyzer_asks_only_intent_and_sets_it():
    p = _Scripted({"intent": "list"})
    qi = DecisionQueryAnalyzer(p, questions=SIRA_QUESTIONS).analyze("What is the value of T3402?")
    assert p.seen == [["intent"]]
    assert qi.intent is Intent.LIST
    assert qi.query_type is QueryType.FACT  # untouched: the rules value


def test_routing_keeps_a_confident_answer():
    a = ConfidenceRoutedQueryAnalyzer(_Scripted({"intent": "compare"}, 0.9), 0.5, questions=SIRA_QUESTIONS)
    qi, trace = a.analyze_with_trace("Explain the attach requirements")
    assert qi.intent is Intent.COMPARE
    assert trace["intent"] == {"source": "model", "confidence": 0.9}


def test_routing_falls_back_to_rules_below_the_floor():
    a = ConfidenceRoutedQueryAnalyzer(_Scripted({"intent": "compare"}, 0.2), 0.5, questions=SIRA_QUESTIONS)
    qi, trace = a.analyze_with_trace("Explain the attach requirements")
    assert qi.intent is Intent.EXPLAIN
    assert trace["intent"] == {"source": "rules", "confidence": 0.2}


def test_below_the_floor_with_no_rule_match_is_other():
    a = ConfidenceRoutedQueryAnalyzer(_Scripted({"intent": "fact"}, 0.1), 0.5, questions=SIRA_QUESTIONS)
    assert a.analyze("T3402").intent is Intent.OTHER


def test_unknown_value_falls_back_to_rules_with_no_confidence_credit():
    a = DecisionQueryAnalyzer(_Scripted({"intent": "translate"}), questions=SIRA_QUESTIONS)
    qi, trace = a.analyze_with_trace("Explain the attach requirements")
    assert qi.intent is Intent.EXPLAIN
    assert trace["intent"]["source"] == "rules"


def test_provider_outage_gives_rules_and_counts_a_fallback():
    a = ConfidenceRoutedQueryAnalyzer(_Down(), 0.5, questions=SIRA_QUESTIONS)
    qi, trace = a.analyze_with_trace("What changed in the latest VZW release?")
    assert qi.intent is Intent.CHANGE
    assert trace["intent"] == {"source": "rules", "confidence": None}
    assert a.fallback_count == 1


def test_knob_factory_passes_questions_through(monkeypatch):
    from core.src.query.analyzer import make_query_analyzer_from_knob
    p = _Scripted({"intent": "fact"})
    for arm in ("decision", "confidence-routed"):
        monkeypatch.setenv("NORA_QUERY_ANALYZER", arm)
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "mock")
        a = make_query_analyzer_from_knob(decision_provider=p, questions=SIRA_QUESTIONS)
        a.analyze("x")
        assert p.seen[-1] == ["intent"], arm
