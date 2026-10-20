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

---

## Task 4: The three-valued `NORA_QUERY_ANALYZER` knob

Design §3: "a **single three-valued knob**, not a boolean flag plus a roster",
named to match `NORA_LLM_PROVIDER` / `NORA_EMBEDDING_PROVIDER` and resolved
CLI > env > config > default like the other `resolve_*` knobs, with the
default staying `rules`.

**Files:**
- Modify: `core/src/env/config.py` (append a section after the LLM block)
- Test: `core/tests/test_query_analyzer_knob.py` (create)

**Interfaces produced:**
- `QUERY_ANALYZERS: tuple[str, ...] = ("rules", "llm", "decision")`
- `DEFAULT_QUERY_ANALYZER: str = "rules"`
- `QUERY_ANALYZER_ENV_VAR: str = "NORA_QUERY_ANALYZER"`
- `resolve_query_analyzer(cli_value=None, config_store_value=None) -> str`
- `DECISION_PROVIDERS: tuple[str, ...] = ("mock", "systemone")`
- `DEFAULT_DECISION_PROVIDER`, `DEFAULT_DECISION_TIMEOUT`,
  `DECISION_PROVIDER_ENV_VAR`, `DECISION_MODEL_ENV_VAR`,
  `DECISION_BASE_URL_ENV_VAR`, `DECISION_TIMEOUT_ENV_VAR`
- `resolve_decision_provider`, `resolve_decision_model`,
  `resolve_decision_base_url`, `resolve_decision_timeout`

Note on tiers: the decision knobs resolve **CLI > env var > default**, two
tiers rather than the LLM chain's four. There is no `config/decision.json`
yet because no deployment needs one, and inventing a file plus a cached
loader plus a `_reset` test hook for a key nobody sets would be machinery
ahead of demand. `resolve_standards_source` in the same module is two-tier for
the same reason. A file tier is additive when phase 3 or 4 wants it.

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_query_analyzer_knob.py`:

```python
"""The three-valued NORA_QUERY_ANALYZER knob and the phase-2 identity
gate (strand jev-experimentation)."""

from __future__ import annotations

import pytest

from core.src.env.config import (
    DEFAULT_QUERY_ANALYZER,
    QUERY_ANALYZER_ENV_VAR,
    QUERY_ANALYZERS,
    resolve_decision_provider,
    resolve_query_analyzer,
)


class TestResolveQueryAnalyzer:
    def test_default_is_rules(self, monkeypatch):
        """Legacy is the default at every phase (design §7) — an
        operator who sets nothing sees today's behaviour."""
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        assert resolve_query_analyzer() == "rules"
        assert DEFAULT_QUERY_ANALYZER == "rules"

    def test_three_values_exactly(self):
        assert QUERY_ANALYZERS == ("rules", "llm", "decision")

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        assert resolve_query_analyzer() == "decision"

    def test_cli_beats_env(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        assert resolve_query_analyzer(cli_value="llm") == "llm"

    def test_config_store_beats_env(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "rules")
        assert resolve_query_analyzer(config_store_value="llm") == "llm"

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        with pytest.raises(ValueError, match="nonsense"):
            resolve_query_analyzer(cli_value="nonsense")

    def test_unknown_env_value_raises(self, monkeypatch):
        """A typo in a shell export must fail loudly, not silently
        serve the default."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decisions")
        with pytest.raises(ValueError, match="decisions"):
            resolve_query_analyzer()

    def test_empty_env_value_falls_through(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "")
        assert resolve_query_analyzer() == "rules"


class TestResolveDecisionProvider:
    def test_default_is_mock(self, monkeypatch):
        """So the `decision` knob value is exercisable on a laptop with
        nothing installed."""
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        assert resolve_decision_provider() == "mock"

    def test_env_var_selects(self, monkeypatch):
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        assert resolve_decision_provider() == "systemone"

    def test_unknown_value_raises(self, monkeypatch):
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        with pytest.raises(ValueError, match="jev"):
            resolve_decision_provider(cli_value="jev")
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
```

Expected: `ImportError: cannot import name 'DEFAULT_QUERY_ANALYZER'`.

- [ ] **Step 3: Write the implementation**

Append to `core/src/env/config.py`, after the LLM-provider section:

```python
# ---------------------------------------------------------------------------
# Query-analyzer selection — strand jev-experimentation, design §3.
#
# A single three-valued knob rather than a boolean flag plus a roster. The
# three values map onto implementations that already exist:
#
#   rules     MockQueryAnalyzer      the live default
#   llm       LLMQueryAnalyzer       written, previously never wired
#   decision  HybridQueryAnalyzer    + a DecisionProvider beneath it
#
# The provider roster did not disappear — it sits one level down, under
# NORA_DECISION_PROVIDER.
# ---------------------------------------------------------------------------

QUERY_ANALYZERS: tuple[str, ...] = ("rules", "llm", "decision")
DEFAULT_QUERY_ANALYZER: str = "rules"
QUERY_ANALYZER_ENV_VAR: str = "NORA_QUERY_ANALYZER"


def resolve_query_analyzer(
    cli_value: str | None = None,
    config_store_value: str | None = None,
) -> str:
    """Resolve which query analyzer serves classification.

    Precedence: CLI flag > Config-page DB > ``NORA_QUERY_ANALYZER`` env
    var > default (``rules``). DB above env var, matching
    ``resolve_llm_provider``'s rationale: a stale shell export must not
    permanently override a value saved through the UI.

    Default is ``rules``, so an operator who sets nothing gets today's
    behaviour byte-for-byte.

    Raises:
        ValueError: A supplied value is not in ``QUERY_ANALYZERS``. A typo
            in a shell export fails loudly rather than silently serving
            the default.
    """
    for label, value in (
        ("--query-analyzer", cli_value),
        ("config_db:query.analyzer", config_store_value),
        (QUERY_ANALYZER_ENV_VAR, os.environ.get(QUERY_ANALYZER_ENV_VAR)),
    ):
        if value:
            if value not in QUERY_ANALYZERS:
                raise ValueError(
                    f"{label}={value!r} not in {QUERY_ANALYZERS}"
                )
            return value
    return DEFAULT_QUERY_ANALYZER


# Decision-provider selection, beneath the `decision` value above.
# Two-tier (CLI > env var > default) rather than the LLM chain's four:
# there is no config/decision.json because no deployment needs one yet,
# and a file plus a cached loader plus a reset hook for a key nobody sets
# would be machinery ahead of demand. A file tier is additive.

DECISION_PROVIDERS: tuple[str, ...] = ("mock", "systemone")
DEFAULT_DECISION_PROVIDER: str = "mock"
DEFAULT_DECISION_TIMEOUT: int = 30
DECISION_PROVIDER_ENV_VAR: str = "NORA_DECISION_PROVIDER"
DECISION_MODEL_ENV_VAR: str = "NORA_DECISION_MODEL"
DECISION_BASE_URL_ENV_VAR: str = "NORA_DECISION_BASE_URL"
DECISION_TIMEOUT_ENV_VAR: str = "NORA_DECISION_TIMEOUT"


def resolve_decision_provider(cli_value: str | None = None) -> str:
    """Resolve which decision provider serves the ``decision`` analyzer.

    Default ``mock`` so the knob value is exercisable with nothing
    installed. ``systemone`` is the HTTP client that lands in phase 3.

    Raises:
        ValueError: Value not in ``DECISION_PROVIDERS``.
    """
    for label, value in (
        ("--decision-provider", cli_value),
        (DECISION_PROVIDER_ENV_VAR,
         os.environ.get(DECISION_PROVIDER_ENV_VAR)),
    ):
        if value:
            if value not in DECISION_PROVIDERS:
                raise ValueError(
                    f"{label}={value!r} not in {DECISION_PROVIDERS}"
                )
            return value
    return DEFAULT_DECISION_PROVIDER


def resolve_decision_model(cli_value: str | None = None) -> str:
    """Resolve the decision model tag. Empty means "the server's own
    default", which is what a single-model deployment wants."""
    return (
        cli_value
        or os.environ.get(DECISION_MODEL_ENV_VAR, "")
    ).strip()


def resolve_decision_base_url(cli_value: str | None = None) -> str:
    """Resolve the decision endpoint's base URL. Must be in-corp — no
    decision-model traffic leaves the network (design §7)."""
    return (
        cli_value
        or os.environ.get(DECISION_BASE_URL_ENV_VAR, "")
    ).strip()


def resolve_decision_timeout(cli_value: int | None = None) -> int:
    """Resolve the decision request timeout in seconds.

    Default 30 rather than the LLM chain's 600: a decision model answers
    in a single forward pass, so a request that takes minutes is a fault,
    not slow work.
    """
    if cli_value:
        return int(cli_value)
    raw = os.environ.get(DECISION_TIMEOUT_ENV_VAR, "")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "%s=%r is not an integer — using %d",
                DECISION_TIMEOUT_ENV_VAR, raw, DEFAULT_DECISION_TIMEOUT,
            )
    return DEFAULT_DECISION_TIMEOUT
```

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
python -m pytest core/tests/test_env_config.py -v
```

Expected: all PASS, including the pre-existing env-config suite.

- [ ] **Step 5: Commit**

```bash
git add core/src/env/config.py core/tests/test_query_analyzer_knob.py
git commit -m "env: three-valued NORA_QUERY_ANALYZER knob + decision chain

rules | llm | decision, resolved CLI > config DB > env var > default,
default rules so an operator who sets nothing gets today's behaviour.
An unrecognised value raises rather than silently serving the
default, so a typo in a shell export fails loudly.

The decision-provider chain sits one level beneath the `decision`
value and is two-tier on purpose — no config/decision.json until a
deployment needs one. Timeout defaults to 30s, not the LLM chain's
600: a decision model answers in one forward pass, so minutes mean a
fault rather than slow work.

Strand: jev-experimentation, phase 2. Design §3."
```

---

## Task 5: Wire the knob, prove the identity gate, document the module

The gate (design §5): "full suite green; with the flag off, behaviour is
byte-identical to today (identity gate, per `corp-db-layer`'s precedent)."

`make_query_analyzer` gains a `decision_provider` argument and keeps its
two existing branches untouched, so every current caller
(`make_query_analyzer(None)`) is unchanged by construction. A second factory
reads the knob and is what the two call sites adopt.

**Import-cycle warning, read rather than assumed:**
`core/src/decision/analyzer.py` imports `MockQueryAnalyzer` from
`core/src/query/analyzer.py`. A module-level import of `HybridQueryAnalyzer`
in `query/analyzer.py` would therefore be circular. The decision import must
be **function-local** inside the factory — the same technique
`PipelineContext.create_llm_provider` uses for `core.src.llm.refusal`.

**Files:**
- Modify: `core/src/query/analyzer.py` (`make_query_analyzer`, + new factory)
- Modify: `core/src/query/pipeline.py:271`
- Modify: `sandbox/sira_query/service.py:1199`
- Create: `core/src/decision/MODULE.md`
- Modify: `core/src/query/MODULE.md`, `docs/compact/MAP.md`
- Test: `core/tests/test_query_analyzer_knob.py` (append)

**Interfaces produced:**
- `make_query_analyzer(llm_provider=None, decision_provider=None)` —
  decision provider supplied → `HybridQueryAnalyzer`; else LLM provider
  supplied → `LLMQueryAnalyzer`; else `MockQueryAnalyzer`
- `make_query_analyzer_from_knob(llm_provider=None,
  decision_provider=None, cli_value=None)` — reads
  `resolve_query_analyzer()` and builds what it names

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_query_analyzer_knob.py`:

```python
from core.src.decision.analyzer import HybridQueryAnalyzer
from core.src.decision.mock_provider import MockDecisionProvider
from core.src.query.analyzer import (
    LLMQueryAnalyzer,
    MockQueryAnalyzer,
    make_query_analyzer,
    make_query_analyzer_from_knob,
)
from core.src.query.schema import QueryType


class _StubLLM:
    model = "stub"

    def complete(self, prompt, system=None, temperature=0.0, **kwargs):
        return ""


class TestMakeQueryAnalyzerArgumentDispatch:
    """The existing factory's behaviour must not move — every current
    caller passes a single positional argument."""

    def test_no_arguments_is_still_rules(self):
        assert isinstance(make_query_analyzer(), MockQueryAnalyzer)

    def test_none_is_still_rules(self):
        assert isinstance(make_query_analyzer(None), MockQueryAnalyzer)

    def test_llm_provider_is_still_llm(self):
        assert isinstance(make_query_analyzer(_StubLLM()), LLMQueryAnalyzer)

    def test_decision_provider_selects_the_hybrid(self):
        got = make_query_analyzer(decision_provider=MockDecisionProvider())
        assert isinstance(got, HybridQueryAnalyzer)

    def test_decision_provider_wins_over_llm(self):
        got = make_query_analyzer(
            _StubLLM(), decision_provider=MockDecisionProvider()
        )
        assert isinstance(got, HybridQueryAnalyzer)


class TestIdentityGate:
    """With the knob unset, classification is byte-identical to today."""

    def test_knob_unset_builds_the_rule_analyzer(self, monkeypatch):
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_knob_unset_classifies_identically_to_today(self, monkeypatch):
        """The gate proper: same verdict on every sample of the phase-1
        labelled set, compared against MockQueryAnalyzer directly."""
        from core.src.eval.classify import load_classify_set

        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        legacy = MockQueryAnalyzer()
        knobbed = make_query_analyzer_from_knob()
        for sample in load_classify_set():
            assert (
                knobbed.analyze(sample.query) == legacy.analyze(sample.query)
            ), sample.sample_id

    def test_pipeline_default_analyzer_is_the_rule_analyzer(self, monkeypatch):
        """pipeline.py's own default must come out of the knob and land
        on today's behaviour."""
        monkeypatch.delenv(QUERY_ANALYZER_ENV_VAR, raising=False)
        from core.src.query.pipeline import QueryPipeline

        import inspect

        source = inspect.getsource(QueryPipeline.__init__)
        assert "make_query_analyzer_from_knob" in source


class TestKnobSelectsEachArm:
    def test_rules(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "rules")
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_llm_with_a_supplied_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "llm")
        got = make_query_analyzer_from_knob(llm_provider=_StubLLM())
        assert isinstance(got, LLMQueryAnalyzer)

    def test_llm_without_a_provider_degrades_to_rules(self, monkeypatch):
        """The sira lane has no LLMProvider wired yet (service.py's own
        recorded follow-up). Selecting `llm` there must degrade, not
        crash the query path."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "llm")
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_decision_defaults_to_the_mock_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.delenv("NORA_DECISION_PROVIDER", raising=False)
        got = make_query_analyzer_from_knob()
        assert isinstance(got, HybridQueryAnalyzer)

    def test_decision_with_a_supplied_provider(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        provider = MockDecisionProvider()
        got = make_query_analyzer_from_knob(decision_provider=provider)
        assert isinstance(got, HybridQueryAnalyzer)

    def test_decision_systemone_without_a_url_degrades_to_rules(
        self, monkeypatch
    ):
        """Phase 2 ships no HTTP provider. Asking for one must degrade
        rather than import something that does not exist."""
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        monkeypatch.setenv("NORA_DECISION_PROVIDER", "systemone")
        monkeypatch.delenv("NORA_DECISION_BASE_URL", raising=False)
        assert isinstance(make_query_analyzer_from_knob(), MockQueryAnalyzer)

    def test_decision_arm_actually_classifies(self, monkeypatch):
        monkeypatch.setenv(QUERY_ANALYZER_ENV_VAR, "decision")
        got = make_query_analyzer_from_knob()
        intent = got.analyze("please summarize the data retry spec")
        assert intent.query_type is QueryType.SUMMARIZE
```

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
```

Expected: `ImportError: cannot import name 'make_query_analyzer_from_knob'`.

- [ ] **Step 3: Write the implementation**

In `core/src/query/analyzer.py`, replace the body of `make_query_analyzer`
with the three-way dispatch and append the knob-reading factory. Keep the
existing docstring's `multi-mno-sira` D-DRAFT-9 posture note — the decision
path mirrors it.

```python
def make_query_analyzer(llm_provider=None, decision_provider=None):
    """Select the query analyzer by NORA's standard rule.

    Decision provider supplied → ``HybridQueryAnalyzer`` (decision model
    for ``query_type`` / ``doc_type_scope``, regex for the free-text
    spans; self-falls-back to keyword analysis on a provider failure).
    LLM provider supplied → ``LLMQueryAnalyzer``. Neither →
    ``MockQueryAnalyzer`` (keyword fallback, no model).

    This makes query analysis follow the same configured-model-or-mock
    posture as the rest of NORA rather than defaulting to keyword
    analysis even when a model is available (multi-mno-sira D-DRAFT-9).
    The decision path mirrors that posture rather than inventing a new
    one, and the fall-through is what keeps every existing caller —
    all of which pass a single positional argument — byte-identical.

    Args:
        llm_provider: An object satisfying the ``LLMProvider`` Protocol
            (has ``complete()``), or ``None``.
        decision_provider: An object satisfying the ``DecisionProvider``
            Protocol (has ``decide()``), or ``None``. Takes precedence
            over ``llm_provider`` when both are supplied.

    Returns:
        A query analyzer with an ``analyze(query) -> QueryIntent`` method.
    """
    if decision_provider is not None:
        # Function-local: core.src.decision.analyzer imports
        # MockQueryAnalyzer from this module, so a module-level import
        # here would be circular.
        from core.src.decision.analyzer import HybridQueryAnalyzer

        return HybridQueryAnalyzer(decision_provider)
    if llm_provider is not None:
        return LLMQueryAnalyzer(llm_provider)
    return MockQueryAnalyzer()


def make_query_analyzer_from_knob(
    llm_provider=None,
    decision_provider=None,
    cli_value=None,
):
    """Select the query analyzer from ``NORA_QUERY_ANALYZER``.

    The knob is three-valued — ``rules`` / ``llm`` / ``decision`` — and
    defaults to ``rules``, so a caller that adopts this function in place
    of ``MockQueryAnalyzer()`` changes nothing for an operator who sets
    no knob. That is the phase-2 identity gate.

    Degrades to ``rules`` rather than raising whenever the selected arm
    has no usable provider: the sira lane has no ``LLMProvider`` wired
    yet, and the ``systemone`` HTTP provider does not land until phase 3.
    A misconfigured classifier must not take the query path down.

    Args:
        llm_provider: Used when the knob says ``llm``.
        decision_provider: Used when the knob says ``decision``; a
            ``MockDecisionProvider`` is built when the knob says
            ``decision`` and none is supplied.
        cli_value: Explicit override, highest precedence.

    Returns:
        A query analyzer with an ``analyze(query) -> QueryIntent`` method.
    """
    from core.src.env.config import (
        resolve_decision_provider,
        resolve_query_analyzer,
    )

    mode = resolve_query_analyzer(cli_value=cli_value)

    if mode == "llm":
        if llm_provider is None:
            logger.warning(
                "NORA_QUERY_ANALYZER=llm but no LLMProvider was supplied "
                "— falling back to rule-based classification"
            )
            return MockQueryAnalyzer()
        return make_query_analyzer(llm_provider=llm_provider)

    if mode == "decision":
        if decision_provider is None:
            decision_provider = _build_decision_provider(
                resolve_decision_provider()
            )
        if decision_provider is None:
            return MockQueryAnalyzer()
        return make_query_analyzer(decision_provider=decision_provider)

    return MockQueryAnalyzer()


def _build_decision_provider(name: str):
    """Construct a decision provider by name, or ``None`` when it cannot
    be built. Phase 2 ships only the mock."""
    if name == "mock":
        from core.src.decision.mock_provider import MockDecisionProvider

        return MockDecisionProvider()
    logger.warning(
        "NORA_DECISION_PROVIDER=%s is not available yet (phase 3) — "
        "falling back to rule-based classification", name,
    )
    return None
```

In `core/src/query/pipeline.py:271`, change:

```python
        self._analyzer = analyzer or MockQueryAnalyzer()
```

to:

```python
        self._analyzer = analyzer or make_query_analyzer_from_knob()
```

and extend that module's import of `MockQueryAnalyzer` to include
`make_query_analyzer_from_knob`. Leave `MockQueryAnalyzer` imported — other
references in the module use it.

In `sandbox/sira_query/service.py`, change both `make_query_analyzer(None)`
call sites (`:1193`'s comment context and `:1199`) to
`make_query_analyzer_from_knob()`, and update the import. Keep the existing
comment recording that wiring an `LLMProvider` here is an outstanding
follow-up — it is still true, and the new `llm`-degrades-to-`rules` branch is
precisely what makes the knob safe to read on this lane before that work
happens.

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
python -m pytest core/tests/ -q
```

Expected: all PASS, no new failures anywhere. The identity-gate test compares
`make_query_analyzer_from_knob()` against `MockQueryAnalyzer()` on all 50
labelled samples and must show zero differences.

- [ ] **Step 5: Write `core/src/decision/MODULE.md`**

Follow the house format used by the other sixteen — `**Purpose**`,
`**Public surface**`, `**Invariants**`, `**Key choices**`. Content:

```markdown
# decision

**Purpose**
Decision models — typed `bool` / `score` / `choice` inference with no token
generation. A decision model has a classification head rather than a token
decoder, so it answers in a single forward pass and its latency does not
scale with the number of options. This module owns the abstraction and the
query-classification application of it. Strand jev-experimentation; design
`docs/compact/strands/jev-experimentation/decision-model-design.md`.

Separate from [llm](../llm/MODULE.md) deliberately: a different abstraction
(typed heads, not completions), and one-package-per-concern is the convention
here.

**Public surface**
- `DecisionProvider` (base.py) — `@runtime_checkable Protocol`; the only
  decision-model interface the rest of the project sees. Attribute `model`,
  method `decide(state, questions) -> list[DecisionAnswer]`. Shaped on
  `/v1/systemone`: a batch of typed questions evaluated in parallel against
  one shared `state`. Implementations may be HTTP clients, in-process
  adapters, or mocks — a candidate without a `/v1/systemone` endpoint
  satisfies this as a thin adapter rather than an HTTP hop.
- `DecisionQuestion(question_id, kind, question, options=())` (base.py) —
  frozen. `kind` is `bool` | `score` | `choice`; `options` is required for
  `choice` and rejected for the others, validated at construction.
- `DecisionAnswer(question_id, value, confidence=0.0)` (base.py) — frozen.
  Matched back to its question by `question_id`, never by position, so a
  provider may reorder or parallelise. `confidence` is the calibrated
  probability where the provider reports one; recorded, not yet acted on —
  an abstain threshold is a later decision.
- `DecisionError` (base.py) — a provider could not answer. Callers degrade
  rather than fail.
- `MockDecisionProvider` (mock_provider.py) — deterministic keyword stub,
  offline, no network; `model`, `call_count`. What the `decision` knob value
  resolves to when no real provider is configured, so the seam is exercisable
  with nothing installed. `choice` returns the first option named in the
  state, else the LAST option.
- `HybridQueryAnalyzer(provider, fallback=None)` (analyzer.py) —
  `analyze(query) -> QueryIntent`. Decision model for `query_type` and
  `doc_type_scope`; the rule-based analyzer for everything else.
- `QUERY_TYPE_QUESTION`, `DOC_SCOPE_QUESTION`, `CLASSIFICATION_QUESTIONS`
  (analyzer.py) — the two choice questions, options generated from
  `QueryType` and `DocTypeScope`.

**Invariants**
- **No external egress.** Every decision provider is self-hosted in-corp.
  Phase 2 ships no HTTP transport at all — importing this package cannot
  cause a network call.
- **Option lists are generated from their enums, never hand-written.** A
  hand-listed option set is exactly how `LLMQueryAnalyzer`'s prompt came to
  omit `summarize` and `fact` (design §2.1). Adding a `QueryType` member
  extends the question with no edit here.
- **`QueryIntent` is the contract and does not change.**
  `HybridQueryAnalyzer` returns the same shape the rule-based analyzer does.
- **A provider failure degrades, never fails.** Any exception from a
  provider, and any answer that is not a member of its enum, falls back to
  the rule-based value — per field, so one bad answer does not discard a
  good one. A classifier outage degrades classification; it does not fail
  the query.
- **Answers are matched by `question_id`.** Never by list position.
- **Free-text fields are not expressible here.** A decision model emits only
  `bool` / `score` / `choice`, so `entities`, `concepts`, `releases` and
  `standards_refs` stay with the regex extractors permanently — not as a
  phase limitation but as a property of the category.

**Key choices**
- `/v1/systemone` as the wire shape, not a bespoke interface: it is the
  de-facto decision-model format, so a candidate swap is a config change.
  Same reasoning that kept the LLM roster vendor-neutral behind
  `/v1/chat/completions` with no SDK dependency.
- `HybridQueryAnalyzer` composes `MockQueryAnalyzer.analyze()` rather than
  reusing its private `_extract_*` methods. One call fills every
  open-extraction field and yields a complete fallback value for free; the
  wasted rule classification costs microseconds.
- The three closed-vocabulary multi-selects (`mnos`, `likely_features`,
  `plan_ids`) are decision-model-ownable as one `bool` question per
  candidate — 24 booleans plus 2 choices against one state, well inside the
  concurrency these servers document — but stay on the rules until phase 3
  so the first measurement has one variable.
```

Also add a one-line pointer to the new module in `core/src/query/MODULE.md`'s
analyzer entry, naming `make_query_analyzer_from_knob` and the knob.

- [ ] **Step 6: Regenerate the map and run the full suite**

```bash
python -m pytest core/tests/ -q
```

Then `/regen-map` — `decision` is a 17th package and `MAP.md`'s module table
and Mermaid graph both need it. The new edges are `decision → query` and
`query → decision` (the latter function-local, to break the cycle); note that
in the map rather than leaving a reader to discover it.

- [ ] **Step 7: Commit**

```bash
git add core/src/query/analyzer.py core/src/query/pipeline.py \
    core/src/query/MODULE.md core/src/decision/MODULE.md \
    sandbox/sira_query/service.py docs/compact/MAP.md \
    core/tests/test_query_analyzer_knob.py
git commit -m "query: wire the three-valued analyzer knob, identity gate green

make_query_analyzer gains a decision_provider argument and keeps both
existing branches, so every current caller is unchanged by
construction. make_query_analyzer_from_knob reads
NORA_QUERY_ANALYZER and is what pipeline.py and the sira service
adopt; with the knob unset both land on MockQueryAnalyzer and the
identity gate asserts byte-identical verdicts across all 50 labelled
samples.

Every arm degrades to rules rather than raising when its provider is
missing — the sira lane still has no LLMProvider wired, and the
systemone HTTP provider does not land until phase 3. A misconfigured
classifier must not take the query path down.

The decision import in the factory is function-local because
decision.analyzer imports MockQueryAnalyzer from this module.

Strand: jev-experimentation, phase 2. Design §3, §5."
```

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| §3 `DecisionProvider` protocol speaking `/v1/systemone` | 1 |
| §3 `core/src/decision/` as a 17th package with its own MODULE.md | 1, 5 |
| §5 a mock provider | 2 |
| §2 `QueryIntent` partitioned by output shape | 3 |
| §2 open-extraction fields stay on the regex extractors | 3 |
| §2 the three multi-selects deferred to phase 3 | 3 (asserted by test) |
| §2.1 option sets are schemas, not prose | 3 (generated from enums) |
| §3 three-valued knob, house precedence, default `rules` | 4 |
| §3 roster one level beneath the `decision` value | 4 |
| §3 `make_query_analyzer` signature extension | 5 |
| §5 identity gate — flag off is byte-identical | 5 |
| §5 full suite green | 5 step 6 |
| §7 no external egress | 1 (no transport ships) |
| `CLAUDE.md` MODULE.md ships with its surface | 5 |

Out of scope with reasons: the `systemone` HTTP provider and the three
multi-selects (phase 3); the trial stack and A/B (phase 4); the default flip
(phase 5); a `config/decision.json` tier (no deployment needs one).

**2. Placeholders.** None. Every code step carries runnable code, every
command is exact, and MODULE.md's content is written out rather than
described.

**3. Type consistency.** `DecisionQuestion` / `DecisionAnswer` field names are
identical in Tasks 1–3 and in MODULE.md. `decide(state, questions)` keeps that
signature in the Protocol, the mock, both test doubles and the hybrid's call.
`CLASSIFICATION_QUESTIONS` is a tuple in Task 3's definition, its tests and
MODULE.md. `make_query_analyzer(llm_provider, decision_provider)` and
`make_query_analyzer_from_knob(llm_provider, decision_provider, cli_value)`
match between Task 5's interface block, implementation and tests.

**4. Known risk.** `test_pipeline_default_analyzer_is_the_rule_analyzer`
asserts on `inspect.getsource`, which is brittle — it checks the call site
adopted the factory rather than checking behaviour. It is there because
`QueryPipeline.__init__` requires a graph, embedder and store to construct,
so asserting on the built analyzer needs fixtures the rest of this file does
not have. `core/tests/test_pipeline.py` has those fixtures; moving the
assertion there is the better home if this proves annoying.
