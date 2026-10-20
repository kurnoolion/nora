"""Tests for the decision-model seam (strand jev-experimentation, phase 2)."""

from __future__ import annotations

import pytest

from core.src.decision.base import (
    DecisionAnswer,
    DecisionError,
    DecisionProvider,
    DecisionQuestion,
)


class TestDecisionQuestion:
    def test_choice_question_carries_options(self):
        q = DecisionQuestion(
            question_id="query_type",
            kind="choice",
            question="Which retrieval shape does this query want?",
            options=("fact", "summarize"),
        )
        assert q.options == ("fact", "summarize")

    def test_bool_question_needs_no_options(self):
        q = DecisionQuestion(
            question_id="mentions_vzw", kind="bool", question="Names VZW?"
        )
        assert q.options == ()

    def test_choice_without_options_is_rejected(self):
        """A choice question with no option set has no answer space —
        catching it at construction beats a provider-side surprise."""
        with pytest.raises(ValueError, match="choice"):
            DecisionQuestion(
                question_id="q", kind="choice", question="?", options=()
            )

    def test_options_on_a_noul_is_rejected(self):
        """A noul's two options are implicit, so supplying any is a
        caller error rather than something to quietly drop."""
        with pytest.raises(ValueError, match="options"):
            DecisionQuestion(
                question_id="q", kind="noul", question="?", options=("a",)
            )

    def test_score_without_options_is_rejected(self):
        """A score's options are its ordered level descriptions, which the
        wire format requires — the first version of this validator
        forbade them, which would have made every score question
        unbuildable."""
        with pytest.raises(ValueError, match="options"):
            DecisionQuestion(question_id="q", kind="score", question="?")

    def test_score_with_options_is_accepted(self):
        q = DecisionQuestion(
            question_id="q", kind="score", question="?",
            options=("low", "high"),
        )
        assert q.options == ("low", "high")

    def test_frozen(self):
        q = DecisionQuestion(question_id="q", kind="noul", question="?")
        with pytest.raises(Exception):
            q.question_id = "other"


class TestDecisionProviderProtocol:
    def test_conforming_object_satisfies_the_protocol(self):
        class _Provider:
            model = "stub"

            def decide(self, state, questions):
                return [
                    DecisionAnswer(q.question_id, True, 1.0) for q in questions
                ]

        assert isinstance(_Provider(), DecisionProvider)

    def test_object_without_decide_does_not(self):
        class _NotAProvider:
            model = "stub"

        assert not isinstance(_NotAProvider(), DecisionProvider)


class TestDecisionError:
    def test_is_a_runtime_error(self):
        assert issubclass(DecisionError, RuntimeError)


from core.src.decision.mock_provider import MockDecisionProvider


class TestMockDecisionProvider:
    def setup_method(self):
        self.p = MockDecisionProvider()

    def test_satisfies_the_protocol(self):
        assert isinstance(self.p, DecisionProvider)
        assert self.p.model == "mock"

    def test_answers_every_question_once(self):
        questions = [
            DecisionQuestion("a", "noul", "Names VZW?"),
            DecisionQuestion("b", "score", "How specific?", ("low", "high")),
            DecisionQuestion("c", "choice", "Shape?", ("fact", "general")),
        ]
        answers = self.p.decide("some query", questions)
        assert [a.question_id for a in answers] == ["a", "b", "c"]

    def test_choice_picks_an_option_named_in_the_state(self):
        q = DecisionQuestion("t", "choice", "Shape?", ("fact", "summarize"))
        answer = self.p.decide("please summarize this", [q])[0]
        assert answer.value == "summarize"

    def test_choice_falls_back_to_the_last_option(self):
        """Last rather than first: the caller orders options so the
        catch-all sits last, which is how QueryType itself is ordered
        (GENERAL is its final member)."""
        q = DecisionQuestion("t", "choice", "Shape?", ("fact", "general"))
        answer = self.p.decide("nothing matches here", [q])[0]
        assert answer.value == "general"

    def test_noul_is_keyword_driven(self):
        """The value is a probability of yes, not a bool — that is what
        the wire format returns, and thresholding it is the caller's
        decision."""
        q = DecisionQuestion("vzw", "noul", "Does the query name VZW?")
        assert self.p.decide("VZW retry rules", [q])[0].value == 1.0
        assert self.p.decide("TMO retry rules", [q])[0].value == 0.0

    def test_score_is_constant(self):
        q = DecisionQuestion("s", "score", "How specific?", ("low", "high"))
        assert self.p.decide("anything", [q])[0].value == 0.5

    def test_deterministic_across_calls(self):
        q = DecisionQuestion("t", "choice", "Shape?", ("fact", "general"))
        first = self.p.decide("what is the value of T3402", [q])
        second = self.p.decide("what is the value of T3402", [q])
        assert first == second

    def test_call_count_tracks_requests_not_questions(self):
        q = DecisionQuestion("t", "noul", "?")
        self.p.decide("a", [q, q])
        self.p.decide("b", [q])
        assert self.p.call_count == 2


from core.src.decision.analyzer import (
    CLASSIFICATION_QUESTIONS,
    DOC_SCOPE_QUESTION,
    HybridQueryAnalyzer,
    QUERY_TYPE_QUESTION,
)
from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.schema import DocTypeScope, QueryType


class _ScriptedProvider:
    """Returns fixed values regardless of state; records what it saw."""

    model = "scripted"

    def __init__(self, values, confidence=1.0):
        self._values = values
        self._confidence = confidence
        self.states = []
        self.seen = []

    def decide(self, state, questions):
        self.states.append(state)
        self.seen.append([q.question_id for q in questions])
        return [
            DecisionAnswer(
                q.question_id, self._values[q.question_id], self._confidence
            )
            for q in questions
            if q.question_id in self._values
        ]


class _FailingProvider:
    model = "failing"

    def __init__(self, exc=None):
        self.exc = exc or DecisionError("provider down")

    def decide(self, state, questions):
        raise self.exc


class TestClassificationQuestions:
    def test_query_type_options_are_the_enum(self):
        assert QUERY_TYPE_QUESTION.options == tuple(
            t.value for t in QueryType
        )

    def test_query_type_catch_all_is_last(self):
        """MockDecisionProvider's choice fallback returns the last
        option, and GENERAL is the taxonomy's own catch-all."""
        assert QUERY_TYPE_QUESTION.options[-1] == QueryType.GENERAL.value

    def test_doc_scope_options_are_the_enum(self):
        assert DOC_SCOPE_QUESTION.options == tuple(
            s.value for s in DocTypeScope
        )

    def test_both_questions_are_choices(self):
        assert [q.kind for q in CLASSIFICATION_QUESTIONS] == [
            "choice", "choice"
        ]

    def test_exactly_two_questions_in_phase_2(self):
        """The three closed-vocabulary multi-selects are deferred to
        phase 3 so the first measurement has one variable (design §2)."""
        assert len(CLASSIFICATION_QUESTIONS) == 2


class TestHybridQueryAnalyzer:
    def test_decision_answers_drive_the_two_choice_fields(self):
        provider = _ScriptedProvider(
            {"query_type": "summarize", "doc_type_scope": "both"}
        )
        intent = HybridQueryAnalyzer(provider).analyze("what is T3402?")
        assert intent.query_type is QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.BOTH

    def test_open_extraction_fields_come_from_the_rules(self):
        provider = _ScriptedProvider(
            {"query_type": "general", "doc_type_scope": "requirements"}
        )
        intent = HybridQueryAnalyzer(provider).analyze(
            "How does VZW T3402 differ from 3GPP TS 24.301?"
        )
        assert "T3402" in intent.entities
        assert "3GPP TS 24.301" in intent.standards_refs
        assert intent.mnos == ["VZW"]

    def test_raw_query_preserved(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        intent = HybridQueryAnalyzer(provider).analyze("  spaced query  ")
        assert intent.raw_query == "  spaced query  "

    def test_state_is_the_query(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        HybridQueryAnalyzer(provider).analyze("the query text")
        assert provider.states == ["the query text"]

    def test_one_request_carries_both_questions(self):
        """Parallel questions against one shared state is the whole
        point of the wire format — two round trips would waste it."""
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        HybridQueryAnalyzer(provider).analyze("q")
        assert provider.seen == [["query_type", "doc_type_scope"]]

    def test_provider_failure_falls_back_to_rules(self):
        intent = HybridQueryAnalyzer(_FailingProvider()).analyze(
            "Summarize the data retry spec"
        )
        assert intent.query_type is QueryType.SUMMARIZE  # the rule's answer

    def test_unexpected_provider_exception_also_falls_back(self):
        """A provider is third-party code; a TypeError from it must not
        take the query down."""
        intent = HybridQueryAnalyzer(
            _FailingProvider(TypeError("bad shape"))
        ).analyze("What is the value of T3402?")
        assert intent.query_type is QueryType.FACT

    def test_unparseable_answer_falls_back_to_rules(self):
        provider = _ScriptedProvider(
            {"query_type": "not_a_query_type", "doc_type_scope": "requirements"}
        )
        intent = HybridQueryAnalyzer(provider).analyze(
            "What is the value of T3402?"
        )
        assert intent.query_type is QueryType.FACT

    def test_missing_answer_keeps_the_rule_value(self):
        """A provider that answers one question but not the other must
        not blank the field it skipped."""
        provider = _ScriptedProvider({"query_type": "summarize"})
        intent = HybridQueryAnalyzer(provider).analyze(
            "what test cases cover this?"
        )
        assert intent.query_type is QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.TEST_CASES

    def test_mock_provider_end_to_end(self):
        intent = HybridQueryAnalyzer(MockDecisionProvider()).analyze(
            "please summarize the data retry spec"
        )
        assert intent.query_type is QueryType.SUMMARIZE

    def test_queryintent_contract_unchanged(self):
        """QueryIntent is the contract and does not change (design §7) —
        the hybrid returns the same shape the rules do."""
        rules = MockQueryAnalyzer().analyze("q")
        provider = _ScriptedProvider(
            {"query_type": "general", "doc_type_scope": "requirements"}
        )
        hybrid = HybridQueryAnalyzer(provider).analyze("q")
        assert type(hybrid) is type(rules)
        assert vars(hybrid).keys() == vars(rules).keys()


class TestFallbackAccounting:
    """Per-query fallback is correct serving behaviour, but it makes a
    total provider outage look like a successful run with the
    rule-based numbers. The counter is what lets an eval harness refuse
    to report a contaminated arm."""

    def test_healthy_provider_records_no_fallbacks(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        analyzer = HybridQueryAnalyzer(provider)
        analyzer.analyze("a")
        analyzer.analyze("b")
        assert analyzer.call_count == 2
        assert analyzer.fallback_count == 0

    def test_dead_provider_counts_every_query(self):
        analyzer = HybridQueryAnalyzer(_FailingProvider())
        for q in ("a", "b", "c"):
            analyzer.analyze(q)
        assert analyzer.call_count == 3
        assert analyzer.fallback_count == 3

    def test_unparseable_answer_is_not_a_fallback(self):
        """An answer that arrived but did not parse degrades one field;
        the provider was reachable, so the arm is not contaminated the
        same way."""
        provider = _ScriptedProvider(
            {"query_type": "nonsense", "doc_type_scope": "requirements"}
        )
        analyzer = HybridQueryAnalyzer(provider)
        analyzer.analyze("What is the value of T3402?")
        assert analyzer.fallback_count == 0


class TestOptionDescriptions:
    """Glosses are load-bearing: measured on classify-dev-v1 against a
    live Kev-4B, bare option identifiers scored 0.460 and the same
    options glossed scored 0.760."""

    def test_every_query_type_has_a_gloss(self):
        """Keyed by the enum, so adding a member fails here rather than
        silently shipping a bare option — the same property §2.1 wants
        for the option list itself."""
        from core.src.decision.analyzer import QUERY_TYPE_DESCRIPTIONS
        from core.src.query.schema import QueryType

        assert set(QUERY_TYPE_DESCRIPTIONS) == set(QueryType)
        assert all(QUERY_TYPE_DESCRIPTIONS[t].strip() for t in QueryType)

    def test_every_doc_scope_has_a_gloss(self):
        from core.src.decision.analyzer import DOC_SCOPE_DESCRIPTIONS
        from core.src.query.schema import DocTypeScope

        assert set(DOC_SCOPE_DESCRIPTIONS) == set(DocTypeScope)
        assert all(DOC_SCOPE_DESCRIPTIONS[s].strip() for s in DocTypeScope)

    def test_questions_carry_one_gloss_per_option_in_order(self):
        from core.src.decision.analyzer import (
            DOC_SCOPE_QUESTION,
            QUERY_TYPE_DESCRIPTIONS,
            QUERY_TYPE_QUESTION,
        )
        from core.src.query.schema import QueryType

        assert len(QUERY_TYPE_QUESTION.descriptions) == len(
            QUERY_TYPE_QUESTION.options
        )
        assert len(DOC_SCOPE_QUESTION.descriptions) == len(
            DOC_SCOPE_QUESTION.options
        )
        assert QUERY_TYPE_QUESTION.descriptions == tuple(
            QUERY_TYPE_DESCRIPTIONS[t] for t in QueryType
        )

    def test_partial_descriptions_are_rejected(self):
        """Matched by position, so a short list would gloss the wrong
        option rather than leaving one unglossed."""
        with pytest.raises(ValueError, match="descriptions"):
            DecisionQuestion(
                question_id="q", kind="choice", question="?",
                options=("a", "b"), descriptions=("only one",),
            )

    def test_no_descriptions_is_still_valid(self):
        q = DecisionQuestion(
            question_id="q", kind="choice", question="?", options=("a",)
        )
        assert q.descriptions == ()
