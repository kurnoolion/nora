# Phase 1 — Characterize + Instrument: Implementation Plan

> Trimmed 2026-10-20: the embedded copies of code and tests were removed. The
> plan's intent, tasks, interfaces and acceptance criteria stay; the committed
> source and tests are the authority for the code itself.


**Goal:** Make query classification measurable — fix the prompt defect that
makes two of ten classes unreachable, build the labelled query set, and record
confusion-matrix baselines for the `rules` and `llm` arms.

**Architecture:** Nothing in the serving path changes. A new evaluation
harness in `core/src/eval/` instantiates analyzers directly, runs them over a
committed synthetic labelled set, and reports a per-class confusion matrix
weighted by downstream retrieval-knob divergence. The one production edit is in
`core/src/query/analyzer.py`, where the LLM prompt's `query_type` option list
starts being generated from the `QueryType` enum instead of hand-written.

**Tech Stack:** Python 3, stdlib only (`dataclasses`, `json`, `statistics`,
`argparse`, `time`), pytest. Existing `LLMProvider` Protocol for the `llm`
arm. No new dependency, no model download, no corpus.

**Spec:** `docs/compact/strands/jev-experimentation/decision-model-design.md`
(draft v1.1) — §2.1 the defect, §5 phase 1, §6 measurement, §7 invariants.
Decision drafts: `decisions-draft.md` D-DRAFT-5, 6, 7, 8, 11, 14.

**Plan location note:** This plan lives in the strand directory, because the
strand is the unit of review, the design doc it argues from is its sibling,
and COMPACT's branch-flow rule in `CLAUDE.md` makes the strand artifacts part
of the branch deliverable.

---

## Global Constraints

- **No external egress.** Nothing in phase 1 makes a network call except the
  `llm` arm, which talks to an administrator-supplied in-corp or localhost endpoint
  passed on the command line. Design §7.
- **No proprietary corpus content in the repo.** The committed labelled set
  contains query text only — no requirement bodies, no real requirement IDs,
  no real plan codes. `CLAUDE.md` Conventions; design §7.
- **Placeholder identifiers only in committed fixtures.** Requirement IDs in
  the labelled set take a placeholder shape (`<PREFIX>_REQ_PLAN_<LETTER>_<4
  digits>`) that matches `analyzer._REQ_ID_PATTERN` without naming a real
  plan. Real plan codes are never written into the set; the hygiene test in
  Task 2 enforces this by reading the codes out of `analyzer._PLAN_ALIASES`
  rather than restating them.
- **MNO codes are written as placeholders** in the set (`{MNO_A}` /
  `{MNO_B}` / `{MNO_C}`), filled in at load time from the analyzer's alias
  table. (Revised 2026-10-20; the first version of the set wrote the codes
  out.)
- **Labels are authored from intent, never from rule output.** See the
  warning in Task 2, step 1. This is the single constraint that decides whether
  phase 1 produces a measurement or a tautology.
- **Legacy behaviour is untouched.** Phase 1 adds no flag, no roster and no
  wiring. `core/src/query/pipeline.py` and `nora_store/service.py` are
  not modified. The `NORA_QUERY_ANALYZER` knob (D-DRAFT-14) is phase 2.
- **Python invocation:** no `pyproject.toml` in this repo. Run modules as
  `python -m core.src.<module>.<module>_cli` from the repo root, and tests as
  `python -m pytest core/tests/<file> -v`.
- **`QueryType` has exactly ten members** — `single_doc`, `cross_doc`,
  `cross_mno_comparison`, `release_diff`, `standards_comparison`,
  `traceability`, `feature_level`, `summarize`, `fact`, `general`
  (`core/src/query/schema.py:20`). Every report covers all ten.
- Line references are as of `9303a8e`. Identifiers are stable; line numbers
  are not — re-anchor if the branch moves.

---

## Assumptions, stated explicitly

Three judgement calls the design doc leaves open, resolved here. An executor
should implement these as written; a reviewer who disagrees should say so
before Task 3.

1. **The knob (`NORA_QUERY_ANALYZER`) is phase 2, not phase 1.** Design §2.1
   says "ship the fix in the same change that introduces the knob", because the
   knob is what makes `LLMQueryAnalyzer` reachable. But the phase-1 harness
   constructs analyzers directly, so it does not need the knob to measure the
   `llm` arm — and §5 scopes phase 1 to "no new model, no new dependency" with
   the serving path untouched. The prompt fix still lands in phase 1, because
   the `llm` arm would otherwise be wrong by construction. The knob lands in
   phase 2 with the rest of the seam.
2. **Confusion cost is derived, not invented.** §6 requires a confusion matrix
   "weighted by downstream divergence" but a hand-authored 10×10 cost matrix
   would be 90 made-up numbers. Instead the cost of confusing A for B is the
   fraction of the five per-type retrieval knobs whose values differ between A
   and B. This is objective, and it reproduces both anchor claims in §6
   exactly: `FACT`↔`SUMMARIZE` scores 1.0 (all five knobs differ,
   "catastrophic"), `CROSS_DOC`↔`FEATURE_LEVEL` scores 0.0 (identical
   configurations, "mild").
3. **Rerank is excluded from the five knobs.** `_TYPE_RERANK_ENABLED`
   (`pipeline.py:163`) is deliberately empty, so every class gets the same
   `_DEFAULT_RERANK_ENABLED = True`. A constant column adds 0.0 to every
   pairwise distance; including it would only dilute the others. Its exclusion
   is asserted by a test so that the day someone adds an entry, the test fails
   and the profile gets a sixth knob.

## File Structure

| File | Responsibility |
|---|---|
| `core/src/query/analyzer.py` (modify, ~line 391) | The one production edit: `query_type` option list generated from `QueryType`. |
| `core/tests/test_query_intent.py` (modify) | Prompt/enum agreement test — the regression guard for the defect. |
| `core/src/eval/classify_set.json` (create) | The committed synthetic labelled dev set: 50 samples, 5 per class. Data only. |
| `core/src/eval/classify.py` (create) | Harness library: sample schema + loader, knob profile, `divergence_cost`, runner, report. No I/O beyond reading the set. |
| `core/src/eval/classify_cli.py` (create) | CLI entrypoint: picks an arm, builds a provider, prints the matrix, writes JSON. |
| `core/tests/test_eval_classify.py` (create) | Tests for everything in `classify.py`, including the two agreement tests. |
| `core/src/eval/MODULE.md` (modify) | Public-surface and invariant entries for the new harness. Ships in Task 5 with the surface it describes. |

`classify.py` splits from `classify_cli.py` on the house pattern — library is
importable and testable, CLI owns argument parsing and printing. The labelled
set is a sibling JSON file rather than Python literals so a reviewer can
review and extend it without reading code, matching how
`customizations/profiles/` carries committed curated JSON.

---

## Task 1: The prompt defect — generate the option list from the enum

`QueryType` has ten members; `LLMQueryAnalyzer`'s prompt enumerates eight,
omitting `summarize` and `fact` (design §2.1). The fix is not to type the two
missing strings — it is to stop hand-writing the list, so the class of defect
cannot recur. The defect is currently latent, because `LLMQueryAnalyzer` is
never constructed in a live path (D-DRAFT-11), but the `llm` arm in Task 5
constructs it directly and would otherwise be unable to emit two of ten
classes.

The same bug survived a `/drift-check dev-full`, because drift-check compares
MODULE.md contracts against code, not prompt contents against an enum. The
agreement test in step 1 is the audit coverage that was missing.

**Files:**
- Modify: `core/src/query/analyzer.py:391` (the `query_type:` prompt line)
- Test: `core/tests/test_query_intent.py` (append a class at the end)

**Interfaces:**
- Consumes: `QueryType`, `DocTypeScope` from `core.src.query.schema`;
  `LLMQueryAnalyzer` from `core.src.query.analyzer`.
- Produces: nothing new. `LLMQueryAnalyzer.analyze()` keeps its exact
  signature and return type. Later tasks rely only on that.

- [ ] **Step 1: Write the failing test**

Append to `core/tests/test_query_intent.py`. The prompt is built inside
`analyze()` and not otherwise reachable, so the test captures it with a
recording provider. Returning `""` makes the analyzer fall back to
`MockQueryAnalyzer` — irrelevant here, since the assertion is on the captured
prompt, not the result.

_(Code block omitted; the committed source is the authority.)_

`re` and `DocTypeScope` must be importable in that test module. Check the
existing imports at the top of `test_query_intent.py` and add only what is
missing — as of `9303a8e` the module imports `LLMQueryAnalyzer` and
`MockQueryAnalyzer` from the analyzer but not `re`, and imports `QueryType`
via the pipeline block; add `import re` and extend the schema import to cover
`DocTypeScope`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest core/tests/test_query_intent.py::TestLLMPromptEnumAgreement -v
```

Expected: `test_prompt_offers_every_query_type` FAILS with
`prompt omits QueryType values: ['summarize', 'fact']`.
`test_prompt_offers_no_unknown_query_type` FAILS on the set inequality.
`test_prompt_offers_every_doc_type_scope` PASSES already — `DocTypeScope`'s
three values are all present in the current prompt. That is expected: it is a
guard on correct code, not a bug being fixed.

- [ ] **Step 3: Write the minimal implementation**

In `core/src/query/analyzer.py`, inside `LLMQueryAnalyzer.analyze()`, above the
`prompt = f"""..."""` assignment, add:

_(Code block omitted; the committed source is the authority.)_

Then replace the `query_type` line of the prompt (`analyzer.py:391`):

_(Code block omitted; the committed source is the authority.)_

with:

_(Code block omitted; the committed source is the authority.)_

Leave every other prompt line alone — including the `plan_ids` line, whose
literal codes are pre-existing and out of scope here. Confirm `QueryType` is
already imported in `analyzer.py` (it is — `MockQueryAnalyzer` returns its
members); do not add an import.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_query_intent.py -v
python -m pytest core/tests/test_query.py -v
```

Expected: all PASS. `test_query_intent.py` has a `TestMakeQueryAnalyzer` class
that exercises the factory; if anything there asserts on prompt text it will
need re-reading, but as of `9303a8e` it asserts on returned types only.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

---

## Task 2: The labelled dev set + its loader

Design §6: "The labelled set is the instrument and it does not exist yet."
This task creates it — 50 samples, 5 per `QueryType`, each carrying an expected
`QueryType` and `DocTypeScope`. It is committed, synthetic, and content-free:
query text only. The corpus-derived set (recorded user questions, the 18-Q OA
set) is a later, `<env_dir>`-resident artifact and is out of scope here.

**Files:**
- Create: `core/src/eval/classify_set.json`
- Create: `core/src/eval/classify.py`
- Test: `core/tests/test_eval_classify.py`

**Interfaces:**
- Consumes: `QueryType`, `DocTypeScope` from `core.src.query.schema`;
  `_PLAN_ALIASES` from `core.src.query.analyzer` (test only).
- Produces, relied on by Tasks 3–5:
  - `ClassifySample` — frozen dataclass, fields `sample_id: str`,
    `query: str`, `query_type: QueryType`, `doc_type_scope: DocTypeScope`
  - `DEFAULT_SET_PATH: Path` — module-relative path to `classify_set.json`
  - `load_classify_set(path: Path | None = None) -> list[ClassifySample]`

- [ ] **Step 1: Author the labelled set**

> **READ THIS BEFORE WRITING THE FILE.** Every label is the *intent a human
> reads in the query*. It is **not** what `MockQueryAnalyzer` returns. Do not
> run the rules and copy their output; do not adjust a label because the rules
> disagree with it. A set authored from rule output makes the `rules` arm score
> 100% and measures nothing, which defeats the entire phase. The set below is
> already authored this way and includes roughly fifteen deliberate
> disagreements with the current rules — see the sanity check in Task 5,
> step 3.

Create `core/src/eval/classify_set.json` with exactly this content:

_(Code block omitted; the committed source is the authority.)_

Why these labels, where they are not self-evident:

- **c03** names a requirement ID and nothing else — a human reads "look this
  one up in its spec", which is `single_doc`. The rules see no plan alias and
  return `general`.
- **c04** names one spec through two of its aliases (`band 13` and
  `network access` both map to the same plan code). A human reads one spec, so
  `single_doc`. The rules count aliases rather than distinct plan codes and
  return `cross_doc`. This is a genuine defect in the alias-count heuristic,
  found while authoring; record it, do not fix it here.
- **c14** compares one named carrier against the others implicitly — intent is
  cross-MNO, with only one MNO token present.
- **c23** says "the 3GPP standard" without citing a spec number, so
  `_extract_standards` finds nothing and the standards guard cannot fire.
  Intent is still a standards comparison.
- **c26** names both test cases and requirements, so scope is `both`; **c30**
  names only a test plan, so `test_cases`. Everything else defaults to
  `requirements`.
- **c31**–**c35** are `feature_level` rather than `cross_doc`: the user names a
  topic and wants that topic's whole surface, which is what `feature_level`
  means in this taxonomy. The boundary against `cross_doc` is genuinely thin —
  which is exactly why the divergence weighting in Task 3 scores that
  confusion at 0.0.
- **c46**–**c50** are `general`: telecom-adjacent or meta questions with no
  retrieval shape the taxonomy recognises.

- [ ] **Step 2: Write the failing tests**

Create `core/tests/test_eval_classify.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 3: Run the tests to verify they fail**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named
'core.src.eval.classify'`.

- [ ] **Step 4: Write the minimal implementation**

Create `core/src/eval/classify.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: all PASS. If `test_every_query_type_has_five_samples` fails, the
JSON was mistyped — fix the JSON, never the assertion.

- [ ] **Step 6: Commit**

_(Code block omitted; the committed source is the authority.)_

---

## Task 3: Divergence cost — how much a confusion actually costs

Design §6: "not all confusions cost the same because each `QueryType` selects
a different retrieval configuration ... the reported metric must reflect damage
rather than label mismatch."

The cost of predicting B when the truth is A is the fraction of the five
per-type retrieval knobs whose values differ between A and B. The profile is
**derived at import from the live sources**, not copied — a copy would need an
agreement test to stay honest, and deriving is both less code and
structurally correct. Because that means the cost function moves when the
pipeline's tuning moves, the report stamps a profile digest, so two reports are
only compared when they were scored against the same configuration (the
comparability-key pattern `eval/golden.py` already uses).

**Files:**
- Modify: `core/src/eval/classify.py`
- Test: `core/tests/test_eval_classify.py`

**Interfaces:**
- Consumes: `is_broad_query_type`, `DEFAULT_BM25_WEIGHT_BY_TYPE` from
  `core.src.env.config`; `_TYPE_REWRITE_ENABLED`, `_TYPE_MAX_DISTANCE`,
  `_TYPE_DISABLE_GROUPING`, `_TYPE_RERANK_ENABLED` from
  `core.src.query.pipeline`.
- Produces, relied on by Task 4:
  - `KNOB_NAMES: tuple[str, ...]` — the five knob names, in profile order
  - `KNOB_PROFILE: dict[QueryType, tuple]` — one row per `QueryType`
  - `divergence_cost(expected: QueryType, predicted: QueryType) -> float`
    — 0.0 when identical configurations, 1.0 when all five differ
  - `profile_digest() -> str` — 12-hex-char digest of the profile

- [ ] **Step 1: Write the failing tests**

Append to `core/tests/test_eval_classify.py`:

_(Code block omitted; the committed source is the authority.)_

Extend that module's imports:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest core/tests/test_eval_classify.py::TestDivergenceCost -v
```

Expected: collection error — `ImportError: cannot import name 'KNOB_NAMES'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `core/src/eval/classify.py` (and extend its import block):

_(Code block omitted; the committed source is the authority.)_

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: all PASS. If `test_fact_summarize_is_maximal` fails, print both
rows — `KNOB_PROFILE[QueryType.FACT]` and `KNOB_PROFILE[QueryType.SUMMARIZE]`
— and reconcile against `pipeline.py:105/121/132/180` and
`env/config.py:549/629` before changing anything. The design doc asserts those
two classes select opposite configurations; if they no longer do, the design
doc is what needs updating.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

---

## Task 4: The runner and the report

Produces the phase-1 gate artifact: a per-class confusion matrix over all ten
classes, plus weighted cost, scope accuracy, determinism across repeats, and
latency. Design §6 requires all of these, and requires that "does it work",
"is it faster" and "is it more accurate" stay separately reported.

**Files:**
- Modify: `core/src/eval/classify.py`
- Test: `core/tests/test_eval_classify.py`

**Interfaces:**
- Consumes: `ClassifySample`, `divergence_cost`, `profile_digest` from Task 3.
  Any object with `analyze(query: str) -> QueryIntent` — the analyzer
  interface `MockQueryAnalyzer` and `LLMQueryAnalyzer` both satisfy.
- Produces, relied on by Task 5:
  - `ClassifyResult` — dataclass: `sample_id: str`, `query: str`,
    `expected: QueryType`, `predicted: QueryType`,
    `expected_scope: DocTypeScope`, `predicted_scope: DocTypeScope`,
    `cost: float`, `latency_ms: float`, `stable: bool`
  - `ClassifyReport` — dataclass: `arm: str`, `set_id: str`,
    `profile: str`, `repeats: int`, `results: list[ClassifyResult]`;
    properties `accuracy`, `weighted_cost`, `scope_accuracy`,
    `deterministic`, `latency_p50_ms`, `latency_p95_ms`;
    methods `confusion() -> dict[QueryType, dict[QueryType, int]]`,
    `matrix_text() -> str`, `to_dict() -> dict`
  - `run_classify(analyzer, samples, repeats: int = 1, arm: str = "",
    set_id: str = "") -> ClassifyReport`

- [ ] **Step 1: Write the failing tests**

Append to `core/tests/test_eval_classify.py`:

_(Code block omitted; the committed source is the authority.)_

Extend the module imports with `QueryIntent`, `ClassifyReport`,
`ClassifyResult`, `run_classify`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest core/tests/test_eval_classify.py::TestRunClassify -v
```

Expected: collection error — `ImportError: cannot import name 'run_classify'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `core/src/eval/classify.py` (and add `import statistics`,
`import time`, `from dataclasses import field`):

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

_(Code block omitted; the committed source is the authority.)_

---

## Task 5: The CLI, the recorded baselines, and MODULE.md

Closes the phase-1 gate: "baselines recorded with a confusion matrix over all
10 classes" (design §5). The `rules` arm is the live baseline a decision model
must actually beat; the `llm` arm has never been measured at this seam
(D-DRAFT-17), so it answers a standing open question independent of decision
models.

MODULE.md ships here, with the public surface it describes, per `CLAUDE.md`'s
branch-flow rule.

**Files:**
- Create: `core/src/eval/classify_cli.py`
- Modify: `core/src/eval/MODULE.md`
- Modify: `docs/compact/strands/jev-experimentation/journal.md`
- Create: `docs/compact/strands/jev-experimentation/phase-1-baselines.md`
  (since folded into `phase-3-measurement.md`, "Superseded phase-1 baselines")

**Interfaces:**
- Consumes: everything from Tasks 2–4; `MockQueryAnalyzer`,
  `LLMQueryAnalyzer` from `core.src.query.analyzer`; `OllamaProvider`,
  `OpenAICompatibleProvider` from `core.src.llm`; `resolve_llm_*` from
  `core.src.env.config`.
- Produces: `main(argv=None) -> int`, invoked as
  `python -m core.src.eval.classify_cli`.

Provider construction note: the house choke point
`PipelineContext.create_llm_provider` requires a `documents_dir`, and phase 1
is deliberately corpus-free, so this CLI builds its provider directly through
the `resolve_llm_*` chain — the same exception `golden_cli` already takes.

- [ ] **Step 1: Write the CLI**

No test-first cycle for this step: it is argument parsing and printing over
library functions that Tasks 2–4 already tested. The verification is step 2's
real run.

Create `core/src/eval/classify_cli.py`:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 2: Run the `rules` arm**

```bash
python -m core.src.eval.classify_cli --arm rules --repeat 3 \
    --out /tmp/classify-rules.json
```

Expected: exit 0, a 10×10 matrix, `deterministic=True` (the rules are pure
string matching), p50 well under 1 ms.

- [ ] **Step 3: Sanity-check the set against the warning in Task 2**

> **If the `rules` arm scores above roughly 0.90 accuracy, stop.** The set
> was almost certainly authored from rule output rather than from intent, and
> the measurement is worthless. Roughly fifteen of the fifty samples were
> written to disagree with the current rules, so an accuracy in the 0.65–0.80
> band is the expected and healthy result. A score near 1.00 means the labels
> were bent to the implementation; a score near 0.20 means the labels are
> wrong in the other direction. Either way, re-read Task 2, step 1 before
> recording anything.

Record the number either way — a surprising baseline is a finding, not a
failure.

- [ ] **Step 4: Run the `llm` arm**

Needs a reachable LLM endpoint. On a developer machine that is local Ollama; in-corp it is
the internal OpenAI-compatible endpoint. No model download is needed for
phase 1 beyond whatever is already installed.

```bash
python -m core.src.eval.classify_cli --arm llm \
    --llm-provider ollama --model <installed-tag> --repeat 3 \
    --out /tmp/classify-llm.json
```

Expected: exit 0. `deterministic` may well be `False` even at temperature 0.0
— record it, do not treat it as a bug. Latency per query will be orders of
magnitude above the rules arm; that contrast is one of phase 1's three
reportable findings.

If no endpoint is reachable, record the arm as not-run with the reason. It
does not block the gate: the gate is the `rules` baseline plus the matrix.

- [ ] **Step 5: Record the baselines**

Create `docs/compact/strands/jev-experimentation/phase-1-baselines.md` with,
for each arm: the command used, the model identity, the profile digest, the
set id, the headline numbers, the full `matrix_text()` output, and a short
reading of where each arm's errors concentrate. Counts and class names only —
`--out` JSON holds query text and stays out of the repo.

Then append a journal entry to
`docs/compact/strands/jev-experimentation/journal.md` covering: the two
baselines; the c04 alias-count defect found while authoring the set (two
aliases of one plan code counted as two plans, so a single-spec query routes
to `cross_doc`); and whether accuracy and weighted cost disagreed, which is
the first real test of the §6 weighting.

- [ ] **Step 6: Update `core/src/eval/MODULE.md`**

Add to **Public surface**:

_(Code block omitted; the committed source is the authority.)_

Add to **Invariants**:

_(Code block omitted; the committed source is the authority.)_

- [ ] **Step 7: Run the full suite**

```bash
python -m pytest core/tests/ -q
```

Expected: no new failures against the pre-task baseline. Phase 1 touches one
production line (the generated option list) on a path no live caller
constructs, so a failure anywhere outside `test_query_intent.py` and
`test_eval_classify.py` means something unexpected — investigate, do not
suppress.

- [ ] **Step 8: Commit**

_(Code block omitted; the committed source is the authority.)_

---

## Self-Review

**1. Spec coverage.**

| Spec requirement | Task |
|---|---|
| §2.1 fix the `SUMMARIZE`/`FACT` prompt omission | 1 |
| §2.1 "a typed option set is a schema, not prose" — make it unrepeatable | 1 (generated from the enum) |
| Drift-check blind spot: prompt contents vs the enum | 1 (agreement test) |
| §6 labelled set, ~40–60 queries, all 10 classes, `QueryType` + `DocTypeScope` | 2 (50 samples) |
| §7 no proprietary content in fixtures | 2 (hygiene tests) |
| §6 divergence-weighted confusion matrix | 3 |
| §6 per-class matrix over all 10 classes, not overall accuracy | 4 (`confusion()`, `per_class()`) |
| §6 determinism across repeated runs | 4 (`repeats`, `deterministic`) |
| §6 latency per arm | 4 (p50/p95) |
| §6 three questions kept separate | 4 (report fields) + 5 (recorded reading) |
| §6 `rules` arm measured | 5 step 2 |
| §6 `llm` arm measured (D-DRAFT-17's standing question) | 5 step 4 |
| §5 gate: baselines recorded with a 10-class confusion matrix | 5 step 5 |
| `CLAUDE.md` MODULE.md ships with its surface | 5 step 6 |

Deliberately out of scope, each with a reason: the `NORA_QUERY_ANALYZER` knob
(phase 2 — see Assumption 1); the corpus-derived labelled set (`<env_dir>`
material, needs the office environment); extrinsic end-to-end evaluation on
the golden set (§6 requires it, but it needs an ingested corpus and a running
stack — it belongs with phase 4's trial stack); arms 3 and 4, the decision
models (phases 3 and beyond).

**2. Placeholders.** None in the original plan: every code step carried
runnable code and the labelled set was written out in full. Those copies are
omitted from this trimmed version; every command is exact. The one step without a
test-first cycle (Task 5, step 1) says why and names its verification.

**3. Type consistency.** `ClassifySample` fields are used identically in Tasks
2, 4 and 5. `run_classify`'s signature in Task 4's interface block matches its
implementation and both call sites in Task 5. `divergence_cost(expected,
predicted)` keeps that argument order in Task 3's definition and Task 4's call.
`profile_digest()` returns 12 characters in Task 3 and is asserted at 12 in
Task 3's test and stamped in Task 4. `matrix_text()` and `to_dict()` are named
consistently in Tasks 4 and 5 and in MODULE.md.

**4. Known defect found while planning, deliberately not fixed.** The
alias-count heuristic at `analyzer.py` counts `_PLAN_ALIASES` hits rather than
distinct plan codes, so a query naming one spec through two of its aliases
("band 13" and "network access", both mapping to the same code) scores two
signals and routes to `CROSS_DOC`. Sample c04 encodes it. Fixing it would
change rule-based classification behaviour mid-measurement, which would
invalidate the baseline this phase exists to establish. It is recorded in the
journal in Task 5 and belongs in its own change.
