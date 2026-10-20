# Phase 2 — The Decision Seam: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `DecisionProvider` seam, a `HybridQueryAnalyzer`, the
three-valued `NORA_QUERY_ANALYZER` knob and a mock decision provider — with
the knob unset, classification behaviour is byte-identical to today.

**Architecture:** A new `core/src/decision/` package owns the decision
abstraction: a `DecisionProvider` Protocol shaped on `/v1/systemone` (typed
`bool`/`score`/`choice` questions against one shared `state`), a mock
implementation, and `HybridQueryAnalyzer`. The hybrid runs
`MockQueryAnalyzer.analyze()` first to fill the open-extraction fields and
supply a fallback, then replaces `query_type` and `doc_type_scope` with the
decision model's two choice answers. A three-valued knob selects between the
three analyzers; its default is `rules`, which is today's behaviour.

**Tech Stack:** Python 3, stdlib only (`typing.Protocol`, `dataclasses`,
`urllib` when a real HTTP provider lands in phase 3). No new dependency.

**Spec:** `docs/compact/strands/jev-experimentation/decision-model-design.md`
v1.1 — §2 the `QueryIntent` partition, §3 the seam and the knob, §5 phase 2,
§7 invariants. Decision drafts D-DRAFT-7, 9, 14.

---

## Global Constraints

- **Legacy is the default.** `NORA_QUERY_ANALYZER` unset resolves to `rules`.
  An operator who sets nothing gets `MockQueryAnalyzer`, byte-identical.
  Design §7.
- **No external egress.** Phase 2 ships no HTTP provider at all — only the
  Protocol and a mock. Nothing can reach the network.
- **`QueryIntent` is the contract and does not change.** No field added,
  removed or retyped. Design §7.
- **The three closed-vocabulary multi-selects stay on regex.** `mnos`,
  `likely_features` and `plan_ids` are decision-model-ownable (design §2) but
  are deferred to phase 3 so the first measurement has one variable.
- **Knob resolution follows the house chain:** CLI > env var > config file >
  default, with `ValueError` on an unrecognised value — the shape
  `resolve_llm_provider` uses (`core/src/env/config.py:778`).
- **`python -m` invocation**, no `pyproject.toml`. Tests:
  `python -m pytest core/tests/<file> -v`.
- Line references are as of `a9ed255`.

## Assumptions, stated explicitly

1. **`HybridQueryAnalyzer` composes `MockQueryAnalyzer` rather than reusing
   its private extractors.** Design §2 assigns `entities`, `concepts`,
   `releases` and `standards_refs` to "the regex extractor", which lives in
   `MockQueryAnalyzer._extract_*` — private methods. Reaching across a module
   boundary into privates is worse than calling `analyze()` once and replacing
   two fields with `dataclasses.replace`. The rule classifier then runs
   needlessly, which costs microseconds and buys a ready-made fallback value
   for free.
2. **The knob is wired at both call sites in phase 2, not phase 4.** The gate
   is "with the flag off, behaviour is byte-identical", which only means
   something if the flag is actually read. So `pipeline.py:271` and
   `service.py:1199` both become knob-aware, and a test asserts the unset
   case is `MockQueryAnalyzer`.
3. **No named multi-entry roster file yet.** Design §3 says a roster sits
   beneath the `decision` value. Phase 2 implements the single-provider
   `NORA_DECISION_*` chain only; a named roster is premature until phase 3 has
   a second real candidate, and `core/src/llm/` grew the same way — single
   chain first, roster added later by the `llm-model-discovery` strand.
4. **Module placement follows the design's proposal** —
   `core/src/decision/` as a 17th package. §8 open point 1 is still open for
   the architect; this implements the proposal rather than pre-empting the
   adjudication, and the package is small enough to move cheaply.

## File Structure

| File | Responsibility |
|---|---|
| `core/src/decision/__init__.py` (create) | Package marker + re-exports. |
| `core/src/decision/base.py` (create) | `DecisionQuestion`, `DecisionAnswer`, `DecisionProvider` Protocol, `DecisionError`. The wire contract, no transport. |
| `core/src/decision/mock_provider.py` (create) | `MockDecisionProvider` — deterministic, offline, test/dev stand-in. |
| `core/src/decision/analyzer.py` (create) | `HybridQueryAnalyzer`, `QUERY_TYPE_QUESTION`, `DOC_SCOPE_QUESTION`. |
| `core/src/decision/MODULE.md` (create) | The 17th module contract. |
| `core/src/env/config.py` (modify) | `QUERY_ANALYZERS`, `resolve_query_analyzer`, `DECISION_PROVIDERS`, `resolve_decision_*`. |
| `core/src/query/analyzer.py` (modify, `make_query_analyzer`) | `decision_provider` argument + `make_query_analyzer_from_knob`. |
| `core/src/query/pipeline.py` (modify, `:271`) | Knob-aware default analyzer. |
| `sandbox/sira_query/service.py` (modify, `:1199`) | Knob-aware default analyzer on the live lane. |
| `core/tests/test_decision.py` (create) | Protocol, mock provider, hybrid analyzer. |
| `core/tests/test_query_analyzer_knob.py` (create) | Knob resolution + the identity gate. |

---

## Task 1: The wire contract — `DecisionProvider`

Design §3: "A `DecisionProvider` protocol speaking `/v1/systemone` — typed
`bool` / `score` / `choice` questions against one shared `state`." Direct
precedent is `OpenAICompatibleProvider` speaking `/v1/chat/completions` over
stdlib urllib with no SDK. This task ships the types and the Protocol only —
no transport, so nothing here can make a network call.

**Files:**
- Create: `core/src/decision/__init__.py`, `core/src/decision/base.py`
- Test: `core/tests/test_decision.py`

**Interfaces produced** (Tasks 2–4 depend on these exact names):
- `DecisionKind = Literal["bool", "score", "choice"]`
- `DecisionQuestion(question_id: str, kind: DecisionKind, question: str,
  options: tuple[str, ...] = ())` — frozen
- `DecisionAnswer(question_id: str, value: bool | float | str,
  confidence: float = 0.0)` — frozen
- `DecisionError(RuntimeError)`
- `DecisionProvider` Protocol — attribute `model: str`, method
  `decide(state: str, questions: Sequence[DecisionQuestion]) ->
  list[DecisionAnswer]`

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_decision.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.src.decision'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/__init__.py`:

```python
"""Decision models — typed bool/score/choice inference, no token generation.

Strand jev-experimentation. See MODULE.md for the contract and
docs/compact/strands/jev-experimentation/decision-model-design.md for why
this is a separate package from `llm`.
"""

from core.src.decision.base import (
    DecisionAnswer,
    DecisionError,
    DecisionKind,
    DecisionProvider,
    DecisionQuestion,
)

__all__ = [
    "DecisionAnswer",
    "DecisionError",
    "DecisionKind",
    "DecisionProvider",
    "DecisionQuestion",
]
```

Create `core/src/decision/base.py`:

```python
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
        kind: One of `bool`, `score`, `choice`.
        question: Natural-language question text.
        options: The answer space for `choice`; empty for the others.
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
        value: `bool` for a bool question, `float` for a score, one of the
            question's `options` for a choice.
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
                parallel; answers are matched by `question_id`, not order.

        Returns:
            One answer per question.

        Raises:
            DecisionError: The provider could not answer.
        """
        ...
```

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/decision/__init__.py core/src/decision/base.py core/tests/test_decision.py
git commit -m "decision: DecisionProvider protocol shaped on /v1/systemone

A new package for the decision abstraction: typed bool/score/choice
questions evaluated in parallel against one shared state. Types and
the Protocol only — no transport, so importing it cannot cause a
network call. Adopting the de-facto wire format makes a candidate
swap a config change, the same reasoning that kept the LLM roster
vendor-neutral behind /v1/chat/completions.

Strand: jev-experimentation, phase 2. Design §3."
```

---

## Task 2: `MockDecisionProvider`

The offline stand-in, mirroring `MockLLMProvider`'s role: deterministic,
keyword-based, no network. It is what the `decision` knob value resolves to
when no real provider is configured, so the knob is exercisable on a laptop
with nothing installed.

**Files:**
- Create: `core/src/decision/mock_provider.py`
- Test: `core/tests/test_decision.py` (append)

**Interfaces:**
- Consumes: Task 1's types.
- Produces: `MockDecisionProvider()` — attributes `model` (`"mock"`),
  `call_count: int`; method `decide(state, questions)`.

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_decision.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py::TestMockDecisionProvider -v
```

Expected: `ModuleNotFoundError: No module named
'core.src.decision.mock_provider'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/mock_provider.py`:

```python
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


class MockDecisionProvider:
    """Keyword-matching stub. Deterministic, offline, no network.

    `bool` is true when a content word of the question appears in the
    state. `choice` returns the first option named in the state, else the
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
        )

    @staticmethod
    def _keyword_hit(lowered: str, question: str) -> bool:
        """True when any capitalised or all-caps token of the question
        appears in the state. Enough to make bool questions testable
        without pretending to be a classifier."""
        tokens = re.findall(r"\b[A-Z][A-Za-z0-9_]{1,}\b", question)
        return any(t.lower() in lowered for t in tokens if t.lower() not in
                   {"does", "the", "query", "name", "is", "this", "it"})
```

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS. If `test_bool_is_keyword_driven` fails, print the tokens
`_keyword_hit` extracted from `"Does the query name VZW?"` — the stoplist
exists because sentence-initial words are capitalised too.

- [ ] **Step 5: Commit**

```bash
git add core/src/decision/mock_provider.py core/tests/test_decision.py
git commit -m "decision: deterministic offline mock provider

Keyword-matching stub so the decision seam is exercisable with
nothing installed — the role MockLLMProvider plays for the LLM
chain. choice falls back to the LAST option, matching how callers
order their option sets with the catch-all last.

Strand: jev-experimentation, phase 2."
```

---

## Task 3: `HybridQueryAnalyzer`

Design §2: a decision model emits only `bool`/`score`/`choice`, so it cannot
replace the analyzer wholesale. `query_type` and `doc_type_scope` are `choice`
questions it owns; `entities`, `concepts`, `releases` and `standards_refs` are
free-text spans no decision model can produce. The hybrid is the join.

It runs `MockQueryAnalyzer.analyze()` first — one call that fills every
open-extraction field *and* yields a complete fallback `QueryIntent` — then
replaces the two choice fields. The three closed-vocabulary multi-selects
(`mnos`, `likely_features`, `plan_ids`) stay on the rules in phase 2 by
design, so the first measurement has one variable.

**Files:**
- Create: `core/src/decision/analyzer.py`
- Test: `core/tests/test_decision.py` (append)

**Interfaces:**
- Consumes: Task 1's types; `MockQueryAnalyzer` from
  `core.src.query.analyzer`; `QueryIntent`, `QueryType`, `DocTypeScope` from
  `core.src.query.schema`.
- Produces, relied on by Task 5:
  - `QUERY_TYPE_QUESTION: DecisionQuestion` — `question_id="query_type"`,
    options are `tuple(t.value for t in QueryType)`
  - `DOC_SCOPE_QUESTION: DecisionQuestion` — `question_id="doc_type_scope"`,
    options are `tuple(s.value for s in DocTypeScope)`
  - `CLASSIFICATION_QUESTIONS: tuple[DecisionQuestion, ...]` — the two, in
    that order
  - `HybridQueryAnalyzer(provider, fallback=None)` with
    `analyze(query) -> QueryIntent`

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_decision.py`:

```python
from dataclasses import replace

from core.src.decision.analyzer import (
    CLASSIFICATION_QUESTIONS,
    DOC_SCOPE_QUESTION,
    HybridQueryAnalyzer,
    QUERY_TYPE_QUESTION,
)
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
```

Extend that module's imports with `MockQueryAnalyzer` from
`core.src.query.analyzer`.

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py::TestHybridQueryAnalyzer -v
```

Expected: `ModuleNotFoundError: No module named 'core.src.decision.analyzer'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/analyzer.py`:

```python
"""Decision-model-backed query analysis.

Design §2 partitions `QueryIntent` by output shape: `query_type` and
`doc_type_scope` are closed-option choices a decision model owns, while
`entities`, `concepts`, `releases` and `standards_refs` are free-text spans
no decision model can emit. `HybridQueryAnalyzer` is that join.

The three closed-vocabulary multi-selects — `mnos`, `likely_features`,
`plan_ids` — are decision-model-ownable too, as one bool question per
candidate, but stay on the rules here so the first measurement has one
variable (design §2, phase-1 scope note).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from core.src.decision.base import DecisionQuestion
from core.src.query.analyzer import MockQueryAnalyzer
from core.src.query.schema import DocTypeScope, QueryIntent, QueryType

logger = logging.getLogger(__name__)

QUERY_TYPE_QUESTION = DecisionQuestion(
    question_id="query_type",
    kind="choice",
    question=(
        "Which retrieval shape does this question want? Pick the single "
        "best match for the asker's intent."
    ),
    options=tuple(t.value for t in QueryType),
)
"""Options generated from the enum, never hand-listed.

Hand-listing is exactly how `LLMQueryAnalyzer`'s prompt came to omit
`summarize` and `fact` (design §2.1). A typed option set is a schema, not
prose, and this is where that property is realised: adding a `QueryType`
member extends the question with no edit here.
"""

DOC_SCOPE_QUESTION = DecisionQuestion(
    question_id="doc_type_scope",
    kind="choice",
    question=(
        "Which document types should be searched to answer this question?"
    ),
    options=tuple(s.value for s in DocTypeScope),
)

CLASSIFICATION_QUESTIONS: tuple[DecisionQuestion, ...] = (
    QUERY_TYPE_QUESTION,
    DOC_SCOPE_QUESTION,
)
"""Both questions in one request — parallel questions against one shared
state is the mechanism the wire format exists for, so two round trips
would waste it."""


class HybridQueryAnalyzer:
    """Decision model for the choice fields, regex for the spans.

    Falls back to the rule-based analyzer in full whenever the provider
    raises or returns something unparseable, mirroring
    `LLMQueryAnalyzer`'s self-fallback posture. A classifier outage
    degrades classification; it does not fail the query.
    """

    def __init__(self, provider, fallback=None) -> None:
        self._provider = provider
        self._fallback = fallback or MockQueryAnalyzer()

    def analyze(self, query: str) -> QueryIntent:
        """Analyze a query into structured intent.

        Args:
            query: Raw user query. Also the decision model's `state`.

        Returns:
            A `QueryIntent`. Same shape the rule-based analyzer returns —
            `QueryIntent` is the contract and does not change.
        """
        base = self._fallback.analyze(query)
        try:
            answers = self._provider.decide(query, CLASSIFICATION_QUESTIONS)
        except Exception as e:  # provider is third-party code
            logger.warning(
                "Decision provider failed (%s) — falling back to "
                "rule-based classification", e,
            )
            return base

        by_id = {a.question_id: a for a in answers}
        query_type = _coerce(
            by_id.get("query_type"), QueryType, base.query_type
        )
        doc_type_scope = _coerce(
            by_id.get("doc_type_scope"), DocTypeScope, base.doc_type_scope
        )
        logger.info(
            "Decision analysis: type=%s scope=%s model=%s",
            query_type.value, doc_type_scope.value,
            getattr(self._provider, "model", "?"),
        )
        return replace(
            base, query_type=query_type, doc_type_scope=doc_type_scope
        )


def _coerce(answer, enum_cls, fallback):
    """Parse one answer into its enum, keeping the rule's value when the
    answer is absent or not a member.

    A missing answer must not blank the field — the rule-based value is a
    better answer than a default.
    """
    if answer is None:
        return fallback
    try:
        return enum_cls(answer.value)
    except (ValueError, TypeError):
        logger.warning(
            "Decision provider returned %r for %s, which is not a %s — "
            "keeping the rule-based value",
            answer.value, answer.question_id, enum_cls.__name__,
        )
        return fallback
```

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/decision/analyzer.py core/tests/test_decision.py
git commit -m "decision: HybridQueryAnalyzer joins decision choices to regex spans

query_type and doc_type_scope are closed-option choices a decision
model owns; entities/concepts/releases/standards_refs are free-text
spans it cannot emit. The hybrid runs the rule-based analyzer once —
filling every span field and yielding a complete fallback — then
replaces the two choice fields. Both questions go in one request,
since parallel questions against one shared state is what the wire
format exists for. Any provider failure or unparseable answer
degrades to the rule-based value rather than failing the query.

The question's option list is generated from the enum, which is the
property that makes design §2.1's defect unrepeatable on this path.

Strand: jev-experimentation, phase 2. Design §2, §3."
```
