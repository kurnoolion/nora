"""Deterministic offline decision provider.

The `decision` knob value resolves here when no real provider is
configured, so the seam is exercisable with nothing installed — the role
`MockLLMProvider` plays for the LLM chain.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from core.src.decision.base import DecisionAnswer, DecisionQuestion

logger = logging.getLogger(__name__)

_MOCK_SCORE = 0.5
"""Constant for `score` questions. A stub has no basis for a real
magnitude, and a constant is at least honest about that."""

_QUESTION_STOPWORDS = frozenset({
    "does", "the", "query", "name", "is", "this", "it", "which",
    "should", "be", "searched", "to", "answer", "pick", "single",
    "best", "match", "for", "asker", "intent", "document", "types",
    "what", "shape", "and", "a",
})
"""Capitalised words that carry no signal — sentence-initial words are
capitalised too, so without this every noul question matches everything."""


class MockDecisionProvider:
    """Keyword-matching stub. Deterministic, offline, no network.

    `noul` returns 1.0 when a content word of the question appears in the
    state, mirroring the wire format's probability-of-yes rather than a
    bool. `choice` returns the first option named in the state, else the
    LAST option — callers order their options with the catch-all last,
    which is how `QueryType` itself is ordered.
    """

    model = "mock"

    def __init__(self) -> None:
        self.call_count = 0

    def decide(
        self, state: str, questions: Sequence[DecisionQuestion]
    ) -> list[DecisionAnswer]:
        self.call_count += 1
        lowered = state.lower()
        return [self._answer(lowered, q) for q in questions]

    def _answer(self, lowered: str, q: DecisionQuestion) -> DecisionAnswer:
        if q.kind == "choice":
            for option in q.options:
                if option.replace("_", " ") in lowered or option in lowered:
                    return DecisionAnswer(q.question_id, option, 1.0)
            return DecisionAnswer(q.question_id, q.options[-1], 0.0)
        if q.kind == "score":
            return DecisionAnswer(q.question_id, _MOCK_SCORE, 0.0)
        return DecisionAnswer(
            q.question_id, self._keyword_hit(lowered, q.question), 0.0
        )  # noul: probability of yes

    @staticmethod
    def _keyword_hit(lowered: str, question: str) -> float:
        """1.0 when any capitalised or all-caps token of the question
        appears in the state, else 0.0. Enough to make noul questions
        testable without pretending to be a classifier."""
        tokens = re.findall(r"\b[A-Z][A-Za-z0-9_]{1,}\b", question)
        return 1.0 * any(
            t.lower() in lowered
            for t in tokens
            if t.lower() not in _QUESTION_STOPWORDS
        )
