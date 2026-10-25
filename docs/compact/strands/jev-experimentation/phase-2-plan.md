# Phase 2 — The Decision Seam: Implementation Plan

> Trimmed 2026-10-20: the embedded copies of code and tests were removed. The
> plan's intent, tasks, interfaces and acceptance criteria stay; the committed
> source and tests are the authority for the code itself.


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
  An administrator who sets nothing gets `MockQueryAnalyzer`, byte-identical.
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
   `core/src/decision/` as a 17th package. §8 open point 1 is still open; this implements the proposal rather than pre-empting the
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
| `core/src/store_service/service.py` (modify, `:1199`) | Knob-aware default analyzer on the live lane. |
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

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.src.decision'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/__init__.py`:

_(Code block omitted; the committed source is the authority.)_

Create `core/src/decision/base.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

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

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py::TestMockDecisionProvider -v
```

Expected: `ModuleNotFoundError: No module named
'core.src.decision.mock_provider'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/mock_provider.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS. If `test_bool_is_keyword_driven` fails, print the tokens
`_keyword_hit` extracted from `"Does the query name MNO-A?"` — the stoplist
exists because sentence-initial words are capitalised too.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

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

_(Code block omitted; the committed source is the authority.)_

Extend that module's imports with `MockQueryAnalyzer` from
`core.src.query.analyzer`.

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_decision.py::TestHybridQueryAnalyzer -v
```

Expected: `ModuleNotFoundError: No module named 'core.src.decision.analyzer'`.

- [ ] **Step 3: Write the implementation**

Create `core/src/decision/analyzer.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_decision.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

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

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 2: Run to verify it fails**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
```

Expected: `ImportError: cannot import name 'DEFAULT_QUERY_ANALYZER'`.

- [ ] **Step 3: Write the implementation**

Append to `core/src/env/config.py`, after the LLM-provider section:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run to verify it passes**

```bash
python -m pytest core/tests/test_query_analyzer_knob.py -v
python -m pytest core/tests/test_env_config.py -v
```

Expected: all PASS, including the pre-existing env-config suite.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

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
- Modify: `core/src/store_service/service.py:1199`
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

_(Code block omitted; the committed source is the authority.)_

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

_(Code block omitted; the committed source is the authority.)_

In `core/src/query/pipeline.py:271`, change:

_(Code block omitted; the committed source is the authority.)_

to:

_(Code block omitted; the committed source is the authority.)_

and extend that module's import of `MockQueryAnalyzer` to include
`make_query_analyzer_from_knob`. Leave `MockQueryAnalyzer` imported — other
references in the module use it.

In `core/src/store_service/service.py`, change both `make_query_analyzer(None)`
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

_(Code block omitted; the committed source is the authority.)_

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

_(Code block omitted; the committed source is the authority.)_

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

**2. Placeholders.** None in the original plan: every code step carried
runnable code and MODULE.md's content was written out. Those copies are
omitted from this trimmed version; every command is exact.

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
