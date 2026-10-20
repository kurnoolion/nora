"""The decision-model wire contract.

Shaped on `/v1/systemone`, the de-facto decision-model wire format: a batch
of typed questions evaluated in parallel against one shared `state`. Adopting
it means a candidate swap is a config change rather than a code change — the
same reasoning that made `OpenAICompatibleProvider`'s adoption of
`/v1/chat/completions` keep the LLM roster vendor-neutral.

Types and the Protocol only. No transport lives here, so importing this
module cannot cause a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence, runtime_checkable

DecisionKind = Literal["bool", "score", "choice"]
"""The three output shapes a decision model can emit.

A decision model has a typed head, not a token decoder, so these are the
whole answer space. Fields needing free text (QueryIntent's `entities`,
`concepts`, `releases`, `standards_refs`) are therefore not expressible here
and stay with the regex extractors — design §2.
"""


class DecisionError(RuntimeError):
    """A decision provider could not answer.

    Callers are expected to degrade rather than fail: the analyzer path
    falls back to rule-based classification, mirroring
    `LLMQueryAnalyzer`'s posture.
    """


@dataclass(frozen=True)
class DecisionQuestion:
    """One typed question against a shared state.

    Args:
        question_id: Stable key. The answer is matched back by this, not
            by position, so a provider may reorder or parallelise freely.
        kind: One of ``bool``, ``score``, ``choice``.
        question: Natural-language question text.
        options: The answer space for ``choice``; empty for the others.
    """

    question_id: str
    kind: DecisionKind
    question: str
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind == "choice" and not self.options:
            raise ValueError(
                f"question {self.question_id!r}: a choice question needs "
                "a non-empty options tuple"
            )
        if self.kind != "choice" and self.options:
            raise ValueError(
                f"question {self.question_id!r}: options are only "
                f"meaningful for a choice question, not {self.kind!r}"
            )


@dataclass(frozen=True)
class DecisionAnswer:
    """One answer.

    Args:
        question_id: Echoes the question's id.
        value: ``bool`` for a bool question, ``float`` for a score, one of
            the question's ``options`` for a choice.
        confidence: Calibrated probability of the returned value where the
            provider reports one, else 0.0. Recorded rather than acted on in
            phase 2 — the abstain threshold is a later decision.
    """

    question_id: str
    value: bool | float | str
    confidence: float = 0.0


@runtime_checkable
class DecisionProvider(Protocol):
    """The only decision-model interface the rest of the project sees.

    Mirrors `llm.base.LLMProvider`'s role: one Protocol, implementations
    free to be HTTP clients, in-process adapters, or mocks. Candidates
    without a `/v1/systemone` endpoint satisfy this as a thin in-process
    adapter rather than an HTTP hop — design §3.
    """

    model: str

    def decide(
        self, state: str, questions: Sequence[DecisionQuestion]
    ) -> list[DecisionAnswer]:
        """Answer every question against one shared state.

        Args:
            state: The shared context — for query classification, the
                query text.
            questions: Questions to evaluate. A provider may answer them in
                parallel; answers are matched by ``question_id``, not order.

        Returns:
            One answer per question.

        Raises:
            DecisionError: The provider could not answer.
        """
        ...
