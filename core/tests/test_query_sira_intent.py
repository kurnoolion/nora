"""Tests for the answer-intent dimension (strand sira-intent, design §2–§3).

Covers:
- The `Intent` enum and the `QueryIntent.intent` field (default, to_dict).
- The rules intent classifier: one case per intent, via MockQueryAnalyzer.
- `change` parity with `is_release_change_question`, which keeps History
  routing on the rules arm identical to today.
- The change rule still imports from `release_diff`, its old home.
"""

from __future__ import annotations

import pytest

from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.schema import Intent, QueryIntent


def test_intent_members_are_the_designed_set():
    assert [i.value for i in Intent] == [
        "fact", "explain", "list", "compliance", "change", "compare", "other",
    ]


def test_query_intent_defaults_to_other_and_serialises_it():
    qi = QueryIntent(raw_query="x")
    assert qi.intent is Intent.OTHER
    assert qi.to_dict()["intent"] == "other"


@pytest.mark.parametrize("question, expected", [
    ("What is the default value of the T3402 timer?", Intent.FACT),
    ("How many attach retries are allowed?", Intent.FACT),
    ("Explain the SMS over IMS requirements", Intent.EXPLAIN),
    ("Summarize the device management plan", Intent.EXPLAIN),
    ("How does the device handle a network detach?", Intent.EXPLAIN),
    ("List all requirements for data retry", Intent.LIST),
    ("What are all the band 13 requirements?", Intent.LIST),
    ("Does retrying after 12 minutes comply with the attach requirements?", Intent.COMPLIANCE),
    ("Is a device that ignores reject cause 15 compliant?", Intent.COMPLIANCE),
    ("What changed in the latest Verizon release?", Intent.CHANGE),
    ("What is new in the latest VZW release?", Intent.CHANGE),
    ("Compare Verizon and T-Mobile retry timers", Intent.COMPARE),
    ("How does the VZW attach retry differ from ATT?", Intent.COMPARE),
    ("T3402", Intent.OTHER),
])
def test_rules_classify_intent(question, expected):
    assert MockQueryAnalyzer().analyze(question).intent is expected


@pytest.mark.parametrize("question", [
    "what changed?",
    "What changed in the latest release of VZW compared to the previous one?",
    "What is new in the latest VZW release?",
    "Compare VZW retry timers",
    "What changed between VZW and TMO latest releases?",
    "What is the retry timer?",
    "List the requirements added in Feb2026",
    "Explain the delta between releases",
])
def test_change_intent_matches_the_release_change_rule(question):
    from core.src.query.release_diff import is_release_change_question
    qi = MockQueryAnalyzer().analyze(question)
    assert (qi.intent is Intent.CHANGE) == is_release_change_question(question, qi)


def test_change_rule_lives_in_intent_and_is_reexported_by_release_diff():
    from core.src.query import intent, release_diff
    assert release_diff.is_release_change_question is intent.is_release_change_question
