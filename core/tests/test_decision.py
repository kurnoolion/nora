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

    def test_options_on_a_bool_is_rejected(self):
        with pytest.raises(ValueError, match="options"):
            DecisionQuestion(
                question_id="q", kind="bool", question="?", options=("a",)
            )

    def test_frozen(self):
        q = DecisionQuestion(question_id="q", kind="bool", question="?")
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
            DecisionQuestion("a", "bool", "Names VZW?"),
            DecisionQuestion("b", "score", "How specific?"),
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

    def test_bool_is_keyword_driven(self):
        q = DecisionQuestion("vzw", "bool", "Does the query name VZW?")
        assert self.p.decide("VZW retry rules", [q])[0].value is True
        assert self.p.decide("TMO retry rules", [q])[0].value is False

    def test_score_is_constant(self):
        q = DecisionQuestion("s", "score", "How specific?")
        assert self.p.decide("anything", [q])[0].value == 0.5

    def test_deterministic_across_calls(self):
        q = DecisionQuestion("t", "choice", "Shape?", ("fact", "general"))
        first = self.p.decide("what is the value of T3402", [q])
        second = self.p.decide("what is the value of T3402", [q])
        assert first == second

    def test_call_count_tracks_requests_not_questions(self):
        q = DecisionQuestion("t", "bool", "?")
        self.p.decide("a", [q, q])
        self.p.decide("b", [q])
        assert self.p.call_count == 2
