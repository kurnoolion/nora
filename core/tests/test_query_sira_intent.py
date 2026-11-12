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


def test_what_are_the_requirements_is_list():
    from core.src.query.analyzer import MockQueryAnalyzer
    a = MockQueryAnalyzer()
    assert a.analyze("what are the VoLTE emergency call requirements").intent is Intent.LIST
    assert a.analyze("What requirements apply to OTA firmware updates?").intent is Intent.LIST
    assert a.analyze("What is the default value of T3402?").intent is not Intent.LIST


# ── bug reports asking for a verdict are compliance (design §3) ───────

@pytest.mark.parametrize("question", [
    "Device crashes when the network sends an unknown NAS IE. Expected it to ignore the IE. "
    "Is this a real issue on our side?",
    "Tester wants the attach retry after 5 minutes instead of 12. Can you comment on this issue?",
    "The device replies 415 to 3GPP2 SMS over IMS and drops it. Expected delivery. is this bug valid?",
    "Lost service in a tunnel, came back on the same cell, and it re-registered IMS. "
    "Expected it to keep the registration since nothing changed. What do you think, is this true?",
    "REGISTER goes out with a long expiry and the lab says that's wrong. Are we wrong here?",
    "The phone stays in limited service after attach fails. Expected 3G fallback but it never "
    "happens. Thoughts?",
])
def test_bug_reports_asking_for_a_verdict_are_compliance(question):
    assert MockQueryAnalyzer().analyze(question).intent is Intent.COMPLIANCE


def test_a_bug_report_is_never_a_release_change_even_when_it_mentions_one():
    from core.src.query.release_diff import is_release_change_question
    q = ("Since the latest release changed the retry timer the device waits 12 minutes; "
         "expected 5. Is this bug valid?")
    qi = MockQueryAnalyzer().analyze(q)
    assert qi.intent is Intent.COMPLIANCE and not is_release_change_question(q, qi)


@pytest.mark.parametrize("question", [
    "Nothing changed on our side but the device now drops calls. Why?",
    "The cell is unchanged and the device re-registers anyway; how does registration work?",
    "It hasn't changed since the last build, so why does attach fail?",
])
def test_negated_change_words_are_not_a_release_change(question):
    from core.src.query.release_diff import is_release_change_question
    qi = MockQueryAnalyzer().analyze(question)
    assert qi.intent is not Intent.CHANGE and not is_release_change_question(question, qi)


def test_real_change_questions_still_route_to_change():
    a = MockQueryAnalyzer()
    for q in ("what changed?", "What changed in the latest release compared to the previous one?",
              "Nothing else matters: what changed in the data retry plan in the latest release?"):
        assert a.analyze(q).intent is Intent.CHANGE, q


@pytest.mark.parametrize("question", [
    "Is it correct that the IMS registration requirement changed in Sep 2031?",
    "Has the attach timer not changed since the previous release?",
])
def test_release_diff_rule_keeps_mains_routing_where_the_change_rule_differs(question):
    # History routes by main's rule while every intent switch is off.
    from core.src.query.intent import is_release_change_question, release_diff_rule
    qi = MockQueryAnalyzer().analyze(question)
    assert release_diff_rule(question, qi) and not is_release_change_question(question, qi)
