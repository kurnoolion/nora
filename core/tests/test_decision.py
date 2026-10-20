"""Tests for the decision-model seam (strand jev-experimentation, phase 2)."""

from __future__ import annotations

import pytest

from core.src.decision.base import (
    DecisionAnswer,
    DecisionError,
    DecisionProvider,
    DecisionQuestion,
)
from core.src.query.analyzer import _MNO_ALIASES

# An MNO code the rule analyzer recognises, taken from its own alias
# table rather than written out.
_MNO = next(iter(dict.fromkeys(_MNO_ALIASES.values())))


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
            question_id="mentions_mnoa", kind="bool", question="Names MNOA?"
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
            DecisionQuestion("a", "noul", "Names MNOA?"),
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
        q = DecisionQuestion("mnoa", "noul", "Does the query name MNOA?")
        assert self.p.decide("MNOA retry rules", [q])[0].value == 1.0
        assert self.p.decide("MNOB retry rules", [q])[0].value == 0.0

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
    ConfidenceRoutedQueryAnalyzer,
    DecisionQueryAnalyzer,
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


class TestDecisionQueryAnalyzer:
    def test_decision_answers_drive_the_two_choice_fields(self):
        provider = _ScriptedProvider(
            {"query_type": "summarize", "doc_type_scope": "both"}
        )
        intent = DecisionQueryAnalyzer(provider).analyze("what is T3402?")
        assert intent.query_type is QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.BOTH

    def test_open_extraction_fields_come_from_the_rules(self):
        provider = _ScriptedProvider(
            {"query_type": "general", "doc_type_scope": "requirements"}
        )
        intent = DecisionQueryAnalyzer(provider).analyze(
            f"How does {_MNO} T3402 differ from 3GPP TS 24.301?"
        )
        assert "T3402" in intent.entities
        assert "3GPP TS 24.301" in intent.standards_refs
        assert intent.mnos == [_MNO]

    def test_raw_query_preserved(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        intent = DecisionQueryAnalyzer(provider).analyze("  spaced query  ")
        assert intent.raw_query == "  spaced query  "

    def test_state_is_the_query(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        DecisionQueryAnalyzer(provider).analyze("the query text")
        assert provider.states == ["the query text"]

    def test_one_request_carries_both_questions(self):
        """Parallel questions against one shared state is the whole
        point of the wire format — two round trips would waste it."""
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        DecisionQueryAnalyzer(provider).analyze("q")
        assert provider.seen == [["query_type", "doc_type_scope"]]

    def test_provider_failure_falls_back_to_rules(self):
        intent = DecisionQueryAnalyzer(_FailingProvider()).analyze(
            "Summarize the data retry spec"
        )
        assert intent.query_type is QueryType.SUMMARIZE  # the rule's answer

    def test_unexpected_provider_exception_also_falls_back(self):
        """A provider is third-party code; a TypeError from it must not
        take the query down."""
        intent = DecisionQueryAnalyzer(
            _FailingProvider(TypeError("bad shape"))
        ).analyze("What is the value of T3402?")
        assert intent.query_type is QueryType.FACT

    def test_unparseable_answer_falls_back_to_rules(self):
        provider = _ScriptedProvider(
            {"query_type": "not_a_query_type", "doc_type_scope": "requirements"}
        )
        intent = DecisionQueryAnalyzer(provider).analyze(
            "What is the value of T3402?"
        )
        assert intent.query_type is QueryType.FACT

    def test_missing_answer_keeps_the_rule_value(self):
        """A provider that answers one question but not the other must
        not blank the field it skipped."""
        provider = _ScriptedProvider({"query_type": "summarize"})
        intent = DecisionQueryAnalyzer(provider).analyze(
            "what test cases cover this?"
        )
        assert intent.query_type is QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.TEST_CASES

    def test_mock_provider_end_to_end(self):
        intent = DecisionQueryAnalyzer(MockDecisionProvider()).analyze(
            "please summarize the data retry spec"
        )
        assert intent.query_type is QueryType.SUMMARIZE

    def test_queryintent_contract_unchanged(self):
        """QueryIntent is the contract and does not change (design §7) —
        the decision analyzer returns the same shape the rules do."""
        rules = MockQueryAnalyzer().analyze("q")
        provider = _ScriptedProvider(
            {"query_type": "general", "doc_type_scope": "requirements"}
        )
        decided = DecisionQueryAnalyzer(provider).analyze("q")
        assert type(decided) is type(rules)
        assert vars(decided).keys() == vars(rules).keys()


class TestFallbackAccounting:
    """Per-query fallback is correct serving behaviour, but it makes a
    total provider outage look like a successful run with the
    rule-based numbers. The counter is what lets an eval harness refuse
    to report a contaminated arm."""

    def test_healthy_provider_records_no_fallbacks(self):
        provider = _ScriptedProvider(
            {"query_type": "fact", "doc_type_scope": "requirements"}
        )
        analyzer = DecisionQueryAnalyzer(provider)
        analyzer.analyze("a")
        analyzer.analyze("b")
        assert analyzer.call_count == 2
        assert analyzer.fallback_count == 0

    def test_dead_provider_counts_every_query(self):
        analyzer = DecisionQueryAnalyzer(_FailingProvider())
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
        analyzer = DecisionQueryAnalyzer(provider)
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


class _ConfidenceProvider:
    """Answers with a stated value AND a stated confidence per question.

    `_ScriptedProvider` carries one confidence for every answer, which
    cannot express the case this class exists to test: one field the
    model is sure of travelling in the same response as one it is not.
    """

    model = "confidence-scripted"

    def __init__(self, answers):
        self._answers = answers  # {question_id: (value, confidence)}

    def decide(self, state, questions):
        return [
            DecisionAnswer(q.question_id, *self._answers[q.question_id])
            for q in questions
            if q.question_id in self._answers
        ]


class TestConfidenceRoutedQueryAnalyzer:
    """The routing policy: take the model's label where its calibrated
    confidence clears the floor, the rule-based label everywhere else.

    Measured motivation (phase-3-measurement.md): mean confidence 0.425
    on correct answers against 0.268 on incorrect ones. The confidence
    carries signal about its own correctness, which is the one thing the
    rules cannot produce at any price.
    """

    _SURE = ("summarize", 0.9)
    _UNSURE = ("summarize", 0.1)
    _SCOPE_SURE = ("both", 0.9)
    _SCOPE_UNSURE = ("both", 0.1)

    def test_above_the_floor_the_model_answers(self):
        provider = _ConfidenceProvider(
            {"query_type": self._SURE, "doc_type_scope": self._SCOPE_SURE}
        )
        intent = ConfidenceRoutedQueryAnalyzer(provider, 0.5).analyze(
            "please summarize the data retry spec"
        )
        assert intent.query_type is QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.BOTH

    def test_below_the_floor_the_rules_answer(self):
        provider = _ConfidenceProvider(
            {"query_type": self._UNSURE,
             "doc_type_scope": self._SCOPE_UNSURE}
        )
        query = "What is the value of T3402?"
        intent = ConfidenceRoutedQueryAnalyzer(provider, 0.5).analyze(query)
        rules = MockQueryAnalyzer().analyze(query)
        assert intent.query_type is rules.query_type
        assert intent.doc_type_scope is rules.doc_type_scope
        assert intent.query_type is not QueryType.SUMMARIZE

    def test_confidence_exactly_at_the_floor_is_taken(self):
        """`>= floor`, not `> floor`. A floor of 0.5 means "0.5 is good
        enough" — the sweep in phase-3-measurement.md was computed with
        this boundary, so the reported numbers depend on it."""
        provider = _ConfidenceProvider({"query_type": ("summarize", 0.5)})
        intent = ConfidenceRoutedQueryAnalyzer(provider, 0.5).analyze("q")
        assert intent.query_type is QueryType.SUMMARIZE

    def test_routing_is_per_field(self):
        """Both answers travel in one request but route independently:
        the model is sure of the scope and unsure of the type here, and
        there is no reason to discard the answer it is sure of."""
        provider = _ConfidenceProvider(
            {"query_type": self._UNSURE,
             "doc_type_scope": self._SCOPE_SURE}
        )
        intent = ConfidenceRoutedQueryAnalyzer(provider, 0.5).analyze(
            "What is the value of T3402?"
        )
        assert intent.query_type is not QueryType.SUMMARIZE
        assert intent.doc_type_scope is DocTypeScope.BOTH

    def test_open_extraction_fields_still_come_from_the_rules(self):
        provider = _ConfidenceProvider({"query_type": self._SURE})
        intent = ConfidenceRoutedQueryAnalyzer(provider, 0.5).analyze(
            "compare T3402 in 3GPP TS 24.301"
        )
        rules = MockQueryAnalyzer().analyze(
            "compare T3402 in 3GPP TS 24.301"
        )
        assert intent.standards_refs == rules.standards_refs
        assert intent.concepts == rules.concepts

    def test_floor_zero_is_the_unrouted_analyzer(self):
        """The identity property. A floor of 0.0 takes every answer the
        model gives, which is exactly what the `decision` arm measured —
        so the routed arm cannot silently change the unrouted result."""
        answers = {"query_type": ("summarize", 0.0),
                   "doc_type_scope": ("both", 0.0)}
        routed = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider(answers), 0.0
        ).analyze("q")
        plain = DecisionQueryAnalyzer(
            _ConfidenceProvider(answers)
        ).analyze("q")
        assert routed.query_type is plain.query_type
        assert routed.doc_type_scope is plain.doc_type_scope

    def test_the_floor_is_recorded_on_the_instance(self):
        """An eval harness stamps it on the report; a number routed at an
        unstated floor is not reproducible."""
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider({}), 0.35
        )
        assert analyzer.confidence_floor == 0.35


class TestRoutingAccounting:
    """Coverage — how much of the workload the model actually answered —
    is the number that makes a routed result readable. An arm that routes
    2 % to the model and scores well is reporting the rules."""

    _BOTH_SURE = {"query_type": ("summarize", 0.9),
                  "doc_type_scope": ("both", 0.9)}
    _BOTH_UNSURE = {"query_type": ("summarize", 0.1),
                    "doc_type_scope": ("both", 0.1)}

    def test_counts_are_per_question_id(self):
        provider = _ConfidenceProvider(
            {"query_type": ("summarize", 0.9),
             "doc_type_scope": ("both", 0.1)}
        )
        analyzer = ConfidenceRoutedQueryAnalyzer(provider, 0.5)
        analyzer.analyze("q")
        assert analyzer.routed_to_model == {"query_type": 1}
        assert analyzer.routed_to_rules == {"doc_type_scope": 1}

    def test_counts_accumulate_across_queries(self):
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider(self._BOTH_SURE), 0.5
        )
        for q in ("a", "b", "c"):
            analyzer.analyze(q)
        assert analyzer.routed_to_model == {
            "query_type": 3, "doc_type_scope": 3
        }
        assert analyzer.routed_to_rules == {}

    def test_coverage_is_the_fraction_the_model_answered(self):
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider(self._BOTH_UNSURE), 0.5
        )
        analyzer.analyze("q")
        assert analyzer.coverage("query_type") == 0.0
        assert analyzer.coverage("doc_type_scope") == 0.0

    def test_coverage_of_a_field_nobody_asked_about_is_zero(self):
        """Not a crash and not 1.0 — an unasked field was answered by
        the model 0 % of the time."""
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider({}), 0.5
        )
        assert analyzer.coverage("query_type") == 0.0

    def test_routing_below_the_floor_is_not_a_provider_fallback(self):
        """The distinction the eval harness depends on. `fallback_count`
        means the provider was unreachable and the arm is contaminated;
        routing means the provider answered and the policy declined it.
        Conflating them would make a healthy routed run look like the
        D-DRAFT-20 failure and get refused."""
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider(self._BOTH_UNSURE), 0.5
        )
        analyzer.analyze("q")
        assert analyzer.fallback_count == 0
        assert analyzer.routed_to_rules["query_type"] == 1

    def test_a_dead_provider_is_still_a_fallback_not_a_routing(self):
        analyzer = ConfidenceRoutedQueryAnalyzer(_FailingProvider(), 0.5)
        analyzer.analyze("q")
        assert analyzer.fallback_count == 1
        assert analyzer.routed_to_model == {}

    def test_an_unparseable_answer_above_the_floor_routes_to_the_rules(self):
        """It cleared the floor but is not a member of the enum. The
        rule-based value serves, and coverage must not credit the model
        for a value that was discarded."""
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider({"query_type": ("nonsense", 0.9)}), 0.5
        )
        analyzer.analyze("What is the value of T3402?")
        assert analyzer.routed_to_model == {}
        assert analyzer.routed_to_rules["query_type"] == 1

    def test_an_unanswered_field_routes_to_the_rules(self):
        """This provider answers one of the two questions. The silent
        field is a routing, not an error — and counting it is what keeps
        coverage from being computed over a denominator that quietly
        shrinks."""
        analyzer = ConfidenceRoutedQueryAnalyzer(
            _ConfidenceProvider({"query_type": ("summarize", 0.9)}), 0.5
        )
        analyzer.analyze("q")
        assert analyzer.routed_to_model == {"query_type": 1}
        assert analyzer.routed_to_rules == {"doc_type_scope": 1}
