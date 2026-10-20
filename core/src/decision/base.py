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

DecisionKind = Literal["noul", "choice", "score"]
"""The three output shapes a decision model can emit.

The names are the wire format's own, verified against `kev/api.py` at
jaredpalmer/kev `1f04cfa`: a yes/no question is a **`noul`**, not a
`bool`. A server validates `type` against this literal and rejects
anything else with a 422, so the spelling is load-bearing rather than
stylistic.

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
        kind: One of ``noul``, ``choice``, ``score``.
        question: Natural-language question text. Serialised as the wire
            format's ``instructions``.
        options: The answer space. Named options for ``choice``, ordered
            level descriptions for ``score``, empty for ``noul`` (whose
            two options are implicit). Serialised as ``criteria``.
        descriptions: One gloss per option, in the same order, or empty
            for none. The wire format carries these as the values of a
            choice question's ``criteria`` object, and they are not
            decoration: measured on classify-dev-v1, glossing the ten
            `QueryType` options moved a live Kev-4B from 0.460 to 0.760
            accuracy. An option named only by its identifier asks the
            model to guess what the identifier means.
    """

    question_id: str
    kind: DecisionKind
    question: str
    options: tuple[str, ...] = ()
    descriptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind in ("choice", "score") and not self.options:
            raise ValueError(
                f"question {self.question_id!r}: a {self.kind} question "
                "needs a non-empty options tuple"
            )
        if self.kind == "noul" and self.options:
            raise ValueError(
                f"question {self.question_id!r}: a noul question's two "
                "options are implicit, so options must be empty"
            )
        if self.descriptions and len(self.descriptions) != len(self.options):
            raise ValueError(
                f"question {self.question_id!r}: {len(self.descriptions)} "
                f"descriptions for {len(self.options)} options — they are "
                "matched by position, so a partial list would gloss the "
                "wrong option"
            )


@dataclass(frozen=True)
class DecisionAnswer:
    """One answer.

    Args:
        question_id: Echoes the question's id.
        value: One of the question's ``options`` for a choice, the
            probability of yes (``float``) for a noul, the expected level
            index (``float``) for a score. A noul carries a probability
            rather than a bool because that is what the wire format
            returns; thresholding it is the caller's decision.
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
