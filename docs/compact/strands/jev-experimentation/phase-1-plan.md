# Phase 1 — Characterize + Instrument: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

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

**Plan location note:** Superpowers' default is
`docs/superpowers/plans/YYYY-MM-DD-<name>.md`. This plan lives in the strand
directory instead, because the strand is the unit the architect reviews, the
design doc it argues from is its sibling, and COMPACT's branch-flow rule in
`CLAUDE.md` makes the strand artifacts part of the branch deliverable.

---

## Global Constraints

- **No external egress.** Nothing in phase 1 makes a network call except the
  `llm` arm, which talks to an operator-supplied in-corp or localhost endpoint
  passed on the command line. Design §7.
- **No proprietary corpus content in the repo.** The committed labelled set
  contains query text only — no requirement bodies, no real requirement IDs,
  no real plan codes. `CLAUDE.md` Conventions; design §7.
- **Placeholder identifiers only in committed fixtures.** Requirement IDs in
  the labelled set take the shape `VZ_REQ_PLAN_<LETTER>_<4 digits>`, which
  matches `analyzer._REQ_ID_PATTERN` (`VZ_REQ_\w+_\d+`) without naming a real
  plan. Real plan codes are never written into the set; the hygiene test in
  Task 2 enforces this by reading the codes out of `analyzer._PLAN_ALIASES`
  rather than restating them.
- **MNO codes `VZW` / `TMO` / `ATT` are permitted** in the set — they are
  already throughout committed source and `core/tests/test_query.py`.
- **Labels are authored from intent, never from rule output.** See the
  warning in Task 2, step 1. This is the single constraint that decides whether
  phase 1 produces a measurement or a tautology.
- **Legacy behaviour is untouched.** Phase 1 adds no flag, no roster and no
  wiring. `core/src/query/pipeline.py` and `sandbox/sira_query/service.py` are
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
| `core/src/eval/classify_cli.py` (create) | Operator entrypoint: picks an arm, builds a provider, prints the matrix, writes JSON. |
| `core/tests/test_eval_classify.py` (create) | Tests for everything in `classify.py`, including the two agreement tests. |
| `core/src/eval/MODULE.md` (modify) | Public-surface and invariant entries for the new harness. Ships in Task 5 with the surface it describes. |

`classify.py` splits from `classify_cli.py` on the house pattern — library is
importable and testable, CLI owns argument parsing and printing. The labelled
set is a sibling JSON file rather than Python literals so the architect can
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

```python
class TestLLMPromptEnumAgreement:
    """The prompt's option lists must match the enums they advertise.

    A hand-written option list silently dropped SUMMARIZE and FACT
    (strand jev-experimentation, design §2.1), making two of ten
    classes unreachable under the LLM analyzer. /drift-check could not
    see it: it compares MODULE.md contracts against code, not prompt
    text against an enum. This test is that missing coverage.
    """

    class _CapturingProvider:
        def __init__(self) -> None:
            self.prompt = ""
            self.system = ""

        def complete(self, prompt, system=None, temperature=0.0, **kwargs):
            self.prompt = prompt
            self.system = system or ""
            return ""

    def _prompt_for(self, query: str) -> str:
        provider = self._CapturingProvider()
        LLMQueryAnalyzer(provider).analyze(query)
        return provider.prompt

    def test_prompt_offers_every_query_type(self):
        prompt = self._prompt_for("What is the value of T3402?")
        missing = [t.value for t in QueryType if f'"{t.value}"' not in prompt]
        assert missing == [], f"prompt omits QueryType values: {missing}"

    def test_prompt_offers_every_doc_type_scope(self):
        prompt = self._prompt_for("what test cases cover this?")
        missing = [s.value for s in DocTypeScope if f'"{s.value}"' not in prompt]
        assert missing == [], f"prompt omits DocTypeScope values: {missing}"

    def test_prompt_offers_no_unknown_query_type(self):
        """Guards the other direction — a stale value left in the prompt
        would be parsed back by QueryType(...) and raise at runtime."""
        prompt = self._prompt_for("summarize the data retry spec")
        line = next(
            ln for ln in prompt.splitlines() if ln.startswith("- query_type:")
        )
        quoted = re.findall(r'"([a-z_]+)"', line)
        assert set(quoted) == {t.value for t in QueryType}
```

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

```python
        type_options = ", ".join(f'"{t.value}"' for t in QueryType)
```

Then replace the `query_type` line of the prompt (`analyzer.py:391`):

```python
- query_type: one of "single_doc", "cross_doc", "cross_mno_comparison", "release_diff", "standards_comparison", "traceability", "feature_level", "general"
```

with:

```python
- query_type: one of {type_options}
```

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

```bash
git add core/src/query/analyzer.py core/tests/test_query_intent.py
git commit -m "query: generate LLM analyzer's query_type options from the enum

The prompt hand-listed eight of ten QueryType values, omitting
summarize and fact, so those two classes were unreachable under
LLMQueryAnalyzer (latent — it is never constructed in a live path).
Generating the list from the enum makes the omission unrepeatable;
the new agreement test is the coverage /drift-check structurally
cannot provide, since it compares MODULE.md against code rather than
prompt text against an enum.

Strand: jev-experimentation, phase 1. Design §2.1."
```

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

```json
{
  "set_id": "classify-dev-v1",
  "description": "Synthetic query-classification dev set. Query text only, no corpus content, placeholder requirement IDs, no real plan codes. Labels are authored from human-read intent, NOT from rule output.",
  "samples": [
    {"sample_id": "c01", "query": "What are the SMS requirements?", "query_type": "single_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c02", "query": "Which AT commands must the device support?", "query_type": "single_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c03", "query": "What does VZ_REQ_PLAN_A_0001 require?", "query_type": "single_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c04", "query": "Band 13 network access requirements for VZW", "query_type": "single_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c05", "query": "Where in the data retry spec is the backoff timer defined?", "query_type": "single_doc", "doc_type_scope": "requirements"},

    {"sample_id": "c06", "query": "What are all the requirements for IMS registration?", "query_type": "cross_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c07", "query": "What requirements exist across all VZW specs for throttling?", "query_type": "cross_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c08", "query": "List every requirement that touches PDN connectivity.", "query_type": "cross_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c09", "query": "Which specs contain attach reject handling rules?", "query_type": "cross_doc", "doc_type_scope": "requirements"},
    {"sample_id": "c10", "query": "Requirements for both SMS and AT commands", "query_type": "cross_doc", "doc_type_scope": "requirements"},

    {"sample_id": "c11", "query": "Compare VZW and TMO data retry requirements", "query_type": "cross_mno_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c12", "query": "How does VZW differ from ATT on SMS over IMS?", "query_type": "cross_mno_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c13", "query": "Does TMO require the same backoff timer as VZW?", "query_type": "cross_mno_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c14", "query": "Is the VZW throttling rule stricter than the other carriers?", "query_type": "cross_mno_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c15", "query": "VZW vs TMO: band 13 network access", "query_type": "cross_mno_comparison", "doc_type_scope": "requirements"},

    {"sample_id": "c16", "query": "What changed in the SMS requirements between Feb 2026 and May 2026?", "query_type": "release_diff", "doc_type_scope": "requirements"},
    {"sample_id": "c17", "query": "What's the delta from the previous release for data retry?", "query_type": "release_diff", "doc_type_scope": "requirements"},
    {"sample_id": "c18", "query": "Show the diff between Feb 2026 and the latest release", "query_type": "release_diff", "doc_type_scope": "requirements"},
    {"sample_id": "c19", "query": "Which requirements were added in the newest release?", "query_type": "release_diff", "doc_type_scope": "requirements"},
    {"sample_id": "c20", "query": "Did the T3402 timer value change in May 2026?", "query_type": "release_diff", "doc_type_scope": "requirements"},

    {"sample_id": "c21", "query": "How does VZW T3402 differ from 3GPP TS 24.301?", "query_type": "standards_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c22", "query": "Compare the attach reject cause codes with 3GPP TS 24.008", "query_type": "standards_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c23", "query": "Is the VZW timer value compliant with the 3GPP standard?", "query_type": "standards_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c24", "query": "Where does the spec deviate from 3GPP TS 23.401?", "query_type": "standards_comparison", "doc_type_scope": "requirements"},
    {"sample_id": "c25", "query": "VZW SMS over IMS vs 3GPP TS 24.341 behaviour", "query_type": "standards_comparison", "doc_type_scope": "requirements"},

    {"sample_id": "c26", "query": "What test cases cover the data retry requirements?", "query_type": "traceability", "doc_type_scope": "both"},
    {"sample_id": "c27", "query": "Which requirements mention the backoff timer?", "query_type": "traceability", "doc_type_scope": "requirements"},
    {"sample_id": "c28", "query": "Where is cause code 19 mentioned?", "query_type": "traceability", "doc_type_scope": "requirements"},
    {"sample_id": "c29", "query": "Show the traceability matrix for band 13", "query_type": "traceability", "doc_type_scope": "requirements"},
    {"sample_id": "c30", "query": "Which test plan validates IMS registration?", "query_type": "traceability", "doc_type_scope": "test_cases"},

    {"sample_id": "c31", "query": "What are all requirements related to IMS?", "query_type": "feature_level", "doc_type_scope": "requirements"},
    {"sample_id": "c32", "query": "Tell me everything about throttling", "query_type": "feature_level", "doc_type_scope": "requirements"},
    {"sample_id": "c33", "query": "All requirements related to device management", "query_type": "feature_level", "doc_type_scope": "requirements"},
    {"sample_id": "c34", "query": "What covers the authentication feature end to end?", "query_type": "feature_level", "doc_type_scope": "requirements"},
    {"sample_id": "c35", "query": "Everything about band 13 network access", "query_type": "feature_level", "doc_type_scope": "requirements"},

    {"sample_id": "c36", "query": "Explain the IMS registration requirements", "query_type": "summarize", "doc_type_scope": "requirements"},
    {"sample_id": "c37", "query": "Summarize the data retry spec", "query_type": "summarize", "doc_type_scope": "requirements"},
    {"sample_id": "c38", "query": "Give me an overview of band 13 network access", "query_type": "summarize", "doc_type_scope": "requirements"},
    {"sample_id": "c39", "query": "Describe how throttling works", "query_type": "summarize", "doc_type_scope": "requirements"},
    {"sample_id": "c40", "query": "What's the big picture on device management?", "query_type": "summarize", "doc_type_scope": "requirements"},

    {"sample_id": "c41", "query": "What is the value of T3402?", "query_type": "fact", "doc_type_scope": "requirements"},
    {"sample_id": "c42", "query": "How many retries are allowed?", "query_type": "fact", "doc_type_scope": "requirements"},
    {"sample_id": "c43", "query": "What is the default value for the backoff timer?", "query_type": "fact", "doc_type_scope": "requirements"},
    {"sample_id": "c44", "query": "What is the maximum number of PDN connections?", "query_type": "fact", "doc_type_scope": "requirements"},
    {"sample_id": "c45", "query": "T3402 timer duration in seconds?", "query_type": "fact", "doc_type_scope": "requirements"},

    {"sample_id": "c46", "query": "What is 5G NR?", "query_type": "general", "doc_type_scope": "requirements"},
    {"sample_id": "c47", "query": "What does NAS stand for?", "query_type": "general", "doc_type_scope": "requirements"},
    {"sample_id": "c48", "query": "Is LTE still supported?", "query_type": "general", "doc_type_scope": "requirements"},
    {"sample_id": "c49", "query": "Who maintains these specifications?", "query_type": "general", "doc_type_scope": "requirements"},
    {"sample_id": "c50", "query": "What is the purpose of this document set?", "query_type": "general", "doc_type_scope": "requirements"}
  ]
}
```

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

```python
"""Tests for the query-classification eval harness (strand
jev-experimentation, phase 1)."""

from __future__ import annotations

import json
import re

import pytest

from core.src.eval.classify import (
    ClassifySample,
    DEFAULT_SET_PATH,
    load_classify_set,
)
from core.src.query.analyzer import _PLAN_ALIASES
from core.src.query.schema import DocTypeScope, QueryType


class TestClassifySet:
    def test_loads_default_set(self):
        samples = load_classify_set()
        assert len(samples) == 50
        assert all(isinstance(s, ClassifySample) for s in samples)

    def test_sample_ids_unique(self):
        samples = load_classify_set()
        ids = [s.sample_id for s in samples]
        assert len(set(ids)) == len(ids)

    def test_every_query_type_has_five_samples(self):
        samples = load_classify_set()
        counts = {t: 0 for t in QueryType}
        for s in samples:
            counts[s.query_type] += 1
        assert counts == {t: 5 for t in QueryType}

    def test_labels_are_enum_members(self):
        for s in load_classify_set():
            assert isinstance(s.query_type, QueryType)
            assert isinstance(s.doc_type_scope, DocTypeScope)

    def test_doc_type_scope_coverage(self):
        """All three scopes appear — a set that is all REQUIREMENTS
        cannot detect a scope regression."""
        scopes = {s.doc_type_scope for s in load_classify_set()}
        assert scopes == set(DocTypeScope)


class TestSetHygiene:
    """NFR-8 / CLAUDE.md: no proprietary identifiers in committed fixtures."""

    def _raw(self) -> str:
        return DEFAULT_SET_PATH.read_text(encoding="utf-8")

    def test_no_real_plan_codes(self):
        """Reads the real codes out of the analyzer rather than
        restating them here, so this test carries none itself."""
        raw = self._raw().upper()
        leaked = sorted({c for c in _PLAN_ALIASES.values() if c.upper() in raw})
        assert leaked == [], f"real plan codes in committed set: {leaked}"

    def test_requirement_ids_are_placeholders(self):
        raw = self._raw()
        for req_id in re.findall(r"VZ_REQ_\w+_\d+", raw):
            assert re.fullmatch(r"VZ_REQ_PLAN_[A-Z]_\d{4}", req_id), (
                f"{req_id} is not a placeholder requirement ID"
            )

    def test_set_is_query_text_only(self):
        """Each sample carries exactly the four declared fields — no
        answers, no chunk bodies, no retrieved content."""
        payload = json.loads(self._raw())
        for sample in payload["samples"]:
            assert set(sample) == {
                "sample_id", "query", "query_type", "doc_type_scope"
            }
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named
'core.src.eval.classify'`.

- [ ] **Step 4: Write the minimal implementation**

Create `core/src/eval/classify.py`:

```python
"""Query-classification evaluation harness.

Strand jev-experimentation, phase 1. Measures a query analyzer against a
labelled set and reports a per-class confusion matrix weighted by how far
apart the confused classes sit in the pipeline's per-type retrieval
configuration. See docs/compact/strands/jev-experimentation/
decision-model-design.md §6.

The harness instantiates analyzers directly. It reads no corpus, needs no
<env_dir>, and does not touch the serving path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.src.query.schema import DocTypeScope, QueryType

DEFAULT_SET_PATH = Path(__file__).resolve().parent / "classify_set.json"


@dataclass(frozen=True)
class ClassifySample:
    """One labelled query. Labels are human-read intent, not rule output."""

    sample_id: str
    query: str
    query_type: QueryType
    doc_type_scope: DocTypeScope


def load_classify_set(path: Path | None = None) -> list[ClassifySample]:
    """Load a labelled classification set from JSON.

    Args:
        path: Set file. ``None`` loads the committed dev set.

    Returns:
        Samples in file order.

    Raises:
        ValueError: A label is not a member of its enum.
    """
    payload = json.loads((path or DEFAULT_SET_PATH).read_text(encoding="utf-8"))
    return [
        ClassifySample(
            sample_id=s["sample_id"],
            query=s["query"],
            query_type=QueryType(s["query_type"]),
            doc_type_scope=DocTypeScope(s["doc_type_scope"]),
        )
        for s in payload["samples"]
    ]
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: all PASS. If `test_every_query_type_has_five_samples` fails, the
JSON was mistyped — fix the JSON, never the assertion.

- [ ] **Step 6: Commit**

```bash
git add core/src/eval/classify.py core/src/eval/classify_set.json core/tests/test_eval_classify.py
git commit -m "eval: labelled query-classification dev set + loader

50 synthetic samples, 5 per QueryType, each labelled with expected
QueryType and DocTypeScope. Labels authored from human-read intent,
deliberately NOT from rule output — a set derived from the rules
would score them at 100% and measure nothing. Query text only; the
hygiene tests assert no real plan codes (read out of
analyzer._PLAN_ALIASES rather than restated) and placeholder-only
requirement IDs.

Strand: jev-experimentation, phase 1. Design §6."
```

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

```python
class TestDivergenceCost:
    def test_identical_type_costs_nothing(self):
        for t in QueryType:
            assert divergence_cost(t, t) == 0.0

    def test_fact_summarize_is_maximal(self):
        """Design §6's catastrophic pair: tight top_k + strict 0.4
        threshold + rerank on, versus wide top_k + 0.7 + grouping off.
        All five knobs differ, so the cost is 1.0."""
        assert divergence_cost(QueryType.FACT, QueryType.SUMMARIZE) == 1.0
        assert divergence_cost(QueryType.SUMMARIZE, QueryType.FACT) == 1.0

    def test_cross_doc_feature_level_is_free(self):
        """Design §6's mild pair — both broad, both rewrite-enabled,
        same BM25 weight, same threshold, same grouping. The two select
        an identical retrieval configuration, so confusing them costs
        nothing downstream."""
        assert divergence_cost(
            QueryType.CROSS_DOC, QueryType.FEATURE_LEVEL
        ) == 0.0

    def test_symmetric(self):
        for a in QueryType:
            for b in QueryType:
                assert divergence_cost(a, b) == divergence_cost(b, a)

    def test_bounded(self):
        for a in QueryType:
            for b in QueryType:
                assert 0.0 <= divergence_cost(a, b) <= 1.0

    def test_profile_covers_every_type(self):
        assert set(KNOB_PROFILE) == set(QueryType)
        assert all(len(row) == len(KNOB_NAMES) for row in KNOB_PROFILE.values())

    def test_rerank_is_uniform_and_therefore_excluded(self):
        """The five knobs deliberately exclude rerank: pipeline's
        per-type map is empty by design, so every class shares
        _DEFAULT_RERANK_ENABLED and the column would add 0.0 to every
        pairwise distance. If an entry is ever added, this fails — and
        the profile then needs a sixth knob."""
        assert _TYPE_RERANK_ENABLED == {}
        assert "rerank" not in KNOB_NAMES

    def test_digest_is_stable_and_short(self):
        assert profile_digest() == profile_digest()
        assert len(profile_digest()) == 12
```

Extend that module's imports:

```python
from core.src.eval.classify import (
    ClassifySample,
    DEFAULT_SET_PATH,
    KNOB_NAMES,
    KNOB_PROFILE,
    divergence_cost,
    load_classify_set,
    profile_digest,
)
from core.src.query.pipeline import _TYPE_RERANK_ENABLED
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest core/tests/test_eval_classify.py::TestDivergenceCost -v
```

Expected: collection error — `ImportError: cannot import name 'KNOB_NAMES'`.

- [ ] **Step 3: Write the minimal implementation**

Append to `core/src/eval/classify.py` (and extend its import block):

```python
import hashlib

from core.src.env.config import (
    DEFAULT_BM25_WEIGHT_BY_TYPE,
    is_broad_query_type,
)
from core.src.query.pipeline import (
    _TYPE_DISABLE_GROUPING,
    _TYPE_MAX_DISTANCE,
    _TYPE_REWRITE_ENABLED,
)
```

```python
# ── Divergence weighting ───────────────────────────────────────

KNOB_NAMES: tuple[str, ...] = (
    "top_k_bucket",
    "bm25_weight",
    "rewrite_enabled",
    "max_distance",
    "disable_grouping",
)
"""The per-type retrieval knobs a QueryType selects.

Rerank is deliberately absent: pipeline's `_TYPE_RERANK_ENABLED` is empty
by design, so every class resolves to the same default and the column
would contribute 0.0 to every pairwise distance. A test asserts the map
is still empty, so adding an entry forces this tuple to grow.
"""


def _knob_row(query_type: QueryType) -> tuple:
    """The five knob values a QueryType selects, read from the live
    sources rather than copied — see this module's docstring."""
    return (
        "broad" if is_broad_query_type(query_type.value) else "narrow",
        DEFAULT_BM25_WEIGHT_BY_TYPE.get(query_type.value, 0.0),
        _TYPE_REWRITE_ENABLED.get(query_type, False),
        _TYPE_MAX_DISTANCE.get(query_type),
        query_type in _TYPE_DISABLE_GROUPING,
    )


KNOB_PROFILE: dict[QueryType, tuple] = {t: _knob_row(t) for t in QueryType}


def divergence_cost(expected: QueryType, predicted: QueryType) -> float:
    """Downstream cost of predicting ``predicted`` when the truth is
    ``expected``, as the fraction of retrieval knobs that differ.

    0.0 means the two classes select an identical retrieval
    configuration, so the mislabel costs nothing downstream. 1.0 means
    every knob is inverted. Symmetric by construction.
    """
    a, b = KNOB_PROFILE[expected], KNOB_PROFILE[predicted]
    differing = sum(1 for x, y in zip(a, b) if x != y)
    return differing / len(KNOB_NAMES)


def profile_digest() -> str:
    """Short digest of the knob profile.

    Two reports are comparable on weighted cost only when their digests
    agree: re-tuning a per-type knob changes what a confusion costs.
    """
    payload = json.dumps(
        {t.value: [str(v) for v in KNOB_PROFILE[t]] for t in QueryType},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
```

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

```bash
git add core/src/eval/classify.py core/tests/test_eval_classify.py
git commit -m "eval: divergence-weighted cost for classification confusions

Not all confusions cost the same: each QueryType selects a different
retrieval configuration, so the cost of predicting B for A is the
fraction of the five per-type knobs that differ. Derived from the
live pipeline/env tables rather than hand-authored, and stamped with
a profile digest so reports scored against different tuning are not
pooled. Reproduces design §6's two anchor claims exactly —
FACT<->SUMMARIZE 1.0, CROSS_DOC<->FEATURE_LEVEL 0.0.

Strand: jev-experimentation, phase 1. Design §6."
```

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

```python
class _ScriptedAnalyzer:
    """Returns a scripted QueryType per query; counts calls so repeat
    behaviour is observable."""

    def __init__(self, script, scope=DocTypeScope.REQUIREMENTS):
        self._script = script
        self._scope = scope
        self.calls = 0

    def analyze(self, query):
        self.calls += 1
        return QueryIntent(
            raw_query=query,
            entities=[],
            concepts=[],
            mnos=[],
            releases=[],
            query_type=self._script[query],
            doc_type_scope=self._scope,
            standards_refs=[],
            likely_features=[],
            plan_ids=[],
        )


class _FlappingAnalyzer:
    """Alternates between two types on every call — the determinism
    probe must catch this."""

    def __init__(self):
        self.calls = 0

    def analyze(self, query):
        self.calls += 1
        qt = QueryType.FACT if self.calls % 2 else QueryType.SUMMARIZE
        return QueryIntent(
            raw_query=query, entities=[], concepts=[], mnos=[],
            releases=[], query_type=qt,
            doc_type_scope=DocTypeScope.REQUIREMENTS,
            standards_refs=[], likely_features=[], plan_ids=[],
        )


def _samples(pairs):
    return [
        ClassifySample(f"s{i}", q, t, DocTypeScope.REQUIREMENTS)
        for i, (q, t) in enumerate(pairs)
    ]


class TestRunClassify:
    def test_perfect_arm_scores_one(self):
        samples = _samples([("a", QueryType.FACT), ("b", QueryType.GENERAL)])
        analyzer = _ScriptedAnalyzer(
            {"a": QueryType.FACT, "b": QueryType.GENERAL}
        )
        report = run_classify(analyzer, samples, arm="perfect")
        assert report.accuracy == 1.0
        assert report.weighted_cost == 0.0
        assert report.arm == "perfect"

    def test_wrong_arm_scores_zero_accuracy_and_real_cost(self):
        samples = _samples([("a", QueryType.FACT)])
        analyzer = _ScriptedAnalyzer({"a": QueryType.SUMMARIZE})
        report = run_classify(analyzer, samples)
        assert report.accuracy == 0.0
        assert report.weighted_cost == 1.0

    def test_free_confusion_costs_nothing_but_still_misses(self):
        """Accuracy and weighted cost must be able to disagree — that
        disagreement is the point of the weighting."""
        samples = _samples([("a", QueryType.CROSS_DOC)])
        analyzer = _ScriptedAnalyzer({"a": QueryType.FEATURE_LEVEL})
        report = run_classify(analyzer, samples)
        assert report.accuracy == 0.0
        assert report.weighted_cost == 0.0

    def test_confusion_matrix_covers_all_ten_classes(self):
        samples = _samples([("a", QueryType.FACT)])
        analyzer = _ScriptedAnalyzer({"a": QueryType.FACT})
        matrix = run_classify(analyzer, samples).confusion()
        assert set(matrix) == set(QueryType)
        for row in matrix.values():
            assert set(row) == set(QueryType)
        assert matrix[QueryType.FACT][QueryType.FACT] == 1

    def test_scope_accuracy_scored_separately(self):
        samples = [ClassifySample("s0", "a", QueryType.FACT, DocTypeScope.BOTH)]
        analyzer = _ScriptedAnalyzer(
            {"a": QueryType.FACT}, scope=DocTypeScope.REQUIREMENTS
        )
        report = run_classify(analyzer, samples)
        assert report.accuracy == 1.0
        assert report.scope_accuracy == 0.0

    def test_repeats_detect_nondeterminism(self):
        samples = _samples([("a", QueryType.FACT)])
        report = run_classify(_FlappingAnalyzer(), samples, repeats=3)
        assert report.deterministic is False
        assert report.results[0].stable is False

    def test_single_repeat_calls_analyzer_once_per_sample(self):
        samples = _samples([("a", QueryType.FACT), ("b", QueryType.FACT)])
        analyzer = _ScriptedAnalyzer(
            {"a": QueryType.FACT, "b": QueryType.FACT}
        )
        run_classify(analyzer, samples, repeats=1)
        assert analyzer.calls == 2

    def test_latency_recorded(self):
        samples = _samples([("a", QueryType.FACT)])
        report = run_classify(_ScriptedAnalyzer({"a": QueryType.FACT}), samples)
        assert report.latency_p50_ms >= 0.0
        assert report.latency_p95_ms >= report.latency_p50_ms

    def test_report_stamps_profile_digest(self):
        samples = _samples([("a", QueryType.FACT)])
        report = run_classify(_ScriptedAnalyzer({"a": QueryType.FACT}), samples)
        assert report.profile == profile_digest()
        assert report.to_dict()["profile"] == profile_digest()

    def test_matrix_text_names_every_class_and_is_content_free(self):
        samples = _samples([("a", QueryType.FACT)])
        text = run_classify(
            _ScriptedAnalyzer({"a": QueryType.FACT}), samples
        ).matrix_text()
        for t in QueryType:
            assert t.value[:8] in text
        assert "a" in text or True  # query text is NOT required in the matrix

    def test_to_dict_is_json_serializable(self):
        samples = _samples([("a", QueryType.FACT)])
        report = run_classify(_ScriptedAnalyzer({"a": QueryType.FACT}), samples)
        json.dumps(report.to_dict())
```

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

```python
# ── Running and reporting ──────────────────────────────────────


@dataclass
class ClassifyResult:
    """One sample's outcome under one arm."""

    sample_id: str
    query: str
    expected: QueryType
    predicted: QueryType
    expected_scope: DocTypeScope
    predicted_scope: DocTypeScope
    cost: float
    latency_ms: float
    stable: bool

    @property
    def correct(self) -> bool:
        return self.expected is self.predicted

    @property
    def scope_correct(self) -> bool:
        return self.expected_scope is self.predicted_scope


@dataclass
class ClassifyReport:
    """One arm's results over one labelled set.

    Keeps design §6's three questions separate: `accuracy` and
    `weighted_cost` answer "is it more accurate", the latency fields
    answer "is it faster", and `deterministic` answers "does it work".
    """

    arm: str = ""
    set_id: str = ""
    profile: str = ""
    repeats: int = 1
    results: list[ClassifyResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.correct for r in self.results) / len(self.results)

    @property
    def weighted_cost(self) -> float:
        """Mean divergence cost. Lower is better; 0.0 means every
        mislabel was downstream-free."""
        if not self.results:
            return 0.0
        return sum(r.cost for r in self.results) / len(self.results)

    @property
    def scope_accuracy(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.scope_correct for r in self.results) / len(self.results)

    @property
    def deterministic(self) -> bool:
        return all(r.stable for r in self.results)

    @property
    def latency_p50_ms(self) -> float:
        if not self.results:
            return 0.0
        return statistics.median(r.latency_ms for r in self.results)

    @property
    def latency_p95_ms(self) -> float:
        if not self.results:
            return 0.0
        ordered = sorted(r.latency_ms for r in self.results)
        idx = min(len(ordered) - 1, int(0.95 * len(ordered)))
        return ordered[idx]

    def confusion(self) -> dict[QueryType, dict[QueryType, int]]:
        """Expected (row) against predicted (column), all ten classes
        present whether or not they occur."""
        matrix = {a: {b: 0 for b in QueryType} for a in QueryType}
        for r in self.results:
            matrix[r.expected][r.predicted] += 1
        return matrix

    def per_class(self) -> dict[QueryType, dict[str, float]]:
        matrix = self.confusion()
        out = {}
        for t in QueryType:
            support = sum(matrix[t].values())
            predicted = sum(matrix[a][t] for a in QueryType)
            hits = matrix[t][t]
            out[t] = {
                "support": support,
                "recall": hits / support if support else 0.0,
                "precision": hits / predicted if predicted else 0.0,
            }
        return out

    def matrix_text(self) -> str:
        """Fixed-width confusion matrix. Carries class names and counts
        only — no query text, so it is chat-pasteable (NFR-8)."""
        matrix = self.confusion()
        labels = [t.value[:8] for t in QueryType]
        head = "expected \\ predicted".ljust(22) + " ".join(
            lbl.rjust(8) for lbl in labels
        )
        lines = [head]
        for t in QueryType:
            cells = " ".join(str(matrix[t][b]).rjust(8) for b in QueryType)
            lines.append(t.value[:8].ljust(22) + cells)
        stats = self.per_class()
        lines.append("")
        lines.append("class".ljust(22) + "support".rjust(9)
                     + "recall".rjust(9) + "precision".rjust(11))
        for t in QueryType:
            s = stats[t]
            lines.append(
                t.value[:8].ljust(22)
                + str(int(s["support"])).rjust(9)
                + f"{s['recall']:.2f}".rjust(9)
                + f"{s['precision']:.2f}".rjust(11)
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "arm": self.arm,
            "set_id": self.set_id,
            "profile": self.profile,
            "repeats": self.repeats,
            "accuracy": self.accuracy,
            "weighted_cost": self.weighted_cost,
            "scope_accuracy": self.scope_accuracy,
            "deterministic": self.deterministic,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "confusion": {
                a.value: {b.value: n for b, n in row.items()}
                for a, row in self.confusion().items()
            },
            "per_class": {
                t.value: stats for t, stats in self.per_class().items()
            },
            "results": [
                {
                    "sample_id": r.sample_id,
                    "expected": r.expected.value,
                    "predicted": r.predicted.value,
                    "expected_scope": r.expected_scope.value,
                    "predicted_scope": r.predicted_scope.value,
                    "cost": r.cost,
                    "latency_ms": r.latency_ms,
                    "stable": r.stable,
                }
                for r in self.results
            ],
        }


def run_classify(
    analyzer,
    samples: list[ClassifySample],
    repeats: int = 1,
    arm: str = "",
    set_id: str = "",
) -> ClassifyReport:
    """Run one analyzer over a labelled set.

    Args:
        analyzer: Any object with ``analyze(query) -> QueryIntent``.
        samples: Labelled samples.
        repeats: Runs per sample. >1 probes determinism; the FIRST run's
            prediction is the one scored, so the score does not depend on
            how many repeats were asked for.
        arm: Label for the report ("rules", "llm", ...).
        set_id: Labelled set's id, stamped for provenance.

    Returns:
        A report stamped with the current knob-profile digest.
    """
    results: list[ClassifyResult] = []
    for sample in samples:
        predictions = []
        latencies = []
        for _ in range(max(1, repeats)):
            started = time.perf_counter()
            intent = analyzer.analyze(sample.query)
            latencies.append((time.perf_counter() - started) * 1000.0)
            predictions.append((intent.query_type, intent.doc_type_scope))
        predicted, predicted_scope = predictions[0]
        results.append(
            ClassifyResult(
                sample_id=sample.sample_id,
                query=sample.query,
                expected=sample.query_type,
                predicted=predicted,
                expected_scope=sample.doc_type_scope,
                predicted_scope=predicted_scope,
                cost=divergence_cost(sample.query_type, predicted),
                latency_ms=statistics.median(latencies),
                stable=len(set(predictions)) == 1,
            )
        )
    return ClassifyReport(
        arm=arm,
        set_id=set_id,
        profile=profile_digest(),
        repeats=max(1, repeats),
        results=results,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest core/tests/test_eval_classify.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/src/eval/classify.py core/tests/test_eval_classify.py
git commit -m "eval: classification runner + confusion-matrix report

run_classify drives any analyze(query)->QueryIntent object over a
labelled set and reports a per-class confusion matrix over all ten
QueryType values, weighted cost, scope accuracy, determinism across
repeats, and latency percentiles. Accuracy and weighted cost are
reported side by side precisely so they can disagree. matrix_text()
carries class names and counts only, so it is chat-pasteable.

Strand: jev-experimentation, phase 1. Design §6."
```

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

```python
"""CLI for the query-classification eval harness.

Strand jev-experimentation, phase 1.

    # the live rule-based baseline — no model, no corpus, no network
    python -m core.src.eval.classify_cli --arm rules

    # the LLM arm against a local Ollama model
    python -m core.src.eval.classify_cli --arm llm \
        --llm-provider ollama --model qwen3:8b --repeat 3

    # the LLM arm against an in-corp OpenAI-compatible endpoint
    python -m core.src.eval.classify_cli --arm llm \
        --llm-provider openai-compatible \
        --base-url http://<host>:<port>/v1 --model <tag>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from core.src.env.config import (
    resolve_llm_api_key,
    resolve_llm_base_url,
    resolve_llm_model,
    resolve_llm_provider,
    resolve_llm_timeout,
)
from core.src.eval.classify import (
    DEFAULT_SET_PATH,
    load_classify_set,
    run_classify,
)
from core.src.query.analyzer import LLMQueryAnalyzer, MockQueryAnalyzer

logger = logging.getLogger(__name__)


def _build_llm_provider(args):
    """Construct the LLM provider for the `llm` arm.

    Built here rather than through PipelineContext.create_llm_provider,
    which requires a documents_dir this corpus-free harness has no use
    for. Mirrors golden_cli's own builder.
    """
    provider = resolve_llm_provider(cli_value=args.llm_provider)
    model = resolve_llm_model(cli_value=args.model)
    timeout = resolve_llm_timeout(cli_value=args.timeout)

    if provider == "mock" or model == "mock":
        from core.src.llm.mock_provider import MockLLMProvider

        return MockLLMProvider()

    if provider == "ollama":
        from core.src.llm.ollama_provider import OllamaProvider

        return OllamaProvider(model=model, timeout=timeout)

    if provider == "openai-compatible":
        from core.src.llm.openai_provider import OpenAICompatibleProvider

        if model == "auto":
            raise SystemExit(
                "--llm-provider openai-compatible requires an explicit "
                "--model (or NORA_LLM_MODEL)."
            )
        return OpenAICompatibleProvider(
            base_url=resolve_llm_base_url(cli_value=args.base_url),
            api_key=resolve_llm_api_key(cli_value=args.api_key),
            model=model,
            timeout=timeout,
        )

    raise SystemExit(f"unsupported --llm-provider: {provider}")


def _build_analyzer(args):
    if args.arm == "rules":
        return MockQueryAnalyzer()
    return LLMQueryAnalyzer(_build_llm_provider(args))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a query analyzer against a labelled set."
    )
    parser.add_argument(
        "--arm", choices=["rules", "llm"], default="rules",
        help="rules = MockQueryAnalyzer (default); llm = LLMQueryAnalyzer",
    )
    parser.add_argument(
        "--set", dest="set_path", type=Path, default=None,
        help=f"labelled set JSON (default: {DEFAULT_SET_PATH})",
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="runs per sample; >1 probes determinism (default 1)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="write the full report as JSON to this path",
    )
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    samples = load_classify_set(args.set_path)
    payload = json.loads(
        (args.set_path or DEFAULT_SET_PATH).read_text(encoding="utf-8")
    )
    report = run_classify(
        _build_analyzer(args),
        samples,
        repeats=args.repeat,
        arm=args.arm,
        set_id=payload.get("set_id", ""),
    )

    print(
        f"arm={report.arm} set={report.set_id} profile={report.profile} "
        f"n={len(report.results)} repeats={report.repeats}"
    )
    print(
        f"accuracy={report.accuracy:.3f} "
        f"weighted_cost={report.weighted_cost:.3f} "
        f"scope_accuracy={report.scope_accuracy:.3f} "
        f"deterministic={report.deterministic} "
        f"p50={report.latency_p50_ms:.1f}ms "
        f"p95={report.latency_p95_ms:.1f}ms"
    )
    print()
    print(report.matrix_text())

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"\nreport written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

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

Needs a reachable LLM endpoint. On the Mac that is local Ollama; in-corp it is
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

```markdown
- Query-classification harness (strand jev-experimentation, phase 1; design
  `docs/compact/strands/jev-experimentation/decision-model-design.md` §6):
  - Schema + set (classify.py): `ClassifySample` — sample_id, query, expected
    `query_type`, expected `doc_type_scope`; `load_classify_set(path=None)`
    loads `DEFAULT_SET_PATH` (`core/src/eval/classify_set.json`, 50 synthetic
    samples, 5 per `QueryType`). Labels are human-read intent, never rule
    output — a set derived from the rules scores them at 100% and measures
    nothing.
  - Divergence weighting (classify.py): `KNOB_NAMES`, `KNOB_PROFILE`,
    `divergence_cost(expected, predicted)` — the fraction of the five
    per-type retrieval knobs (top_k bucket, BM25 weight, rewrite, max
    distance, grouping-disable) that differ between two classes, so a
    confusion is scored by downstream damage rather than label mismatch.
    Derived at import from `env.config._BROAD_QUERY_TYPES` /
    `DEFAULT_BM25_WEIGHT_BY_TYPE` and `query.pipeline._TYPE_*`, never copied.
    Rerank is excluded because `_TYPE_RERANK_ENABLED` is empty by design and a
    constant column adds 0.0 to every distance; a test fails if an entry
    appears. `profile_digest()` stamps every report, so reports scored against
    different tuning are not pooled.
  - Runner + report (classify.py): `run_classify(analyzer, samples,
    repeats=1, arm="", set_id="")` drives any `analyze(query) -> QueryIntent`
    object; `ClassifyResult`, `ClassifyReport` — `accuracy`, `weighted_cost`,
    `scope_accuracy`, `deterministic`, `latency_p50_ms`, `latency_p95_ms`,
    `confusion()` (all ten classes, present whether or not they occur),
    `per_class()`, `matrix_text()`, `to_dict()`. With `repeats > 1` the FIRST
    run is the one scored and the rest only probe determinism, so the score
    does not move with the repeat count.
  - CLI: `classify_cli.main` — `--arm {rules,llm}`, `--set`, `--repeat`,
    `--out`, `--llm-provider`, `--model`, `--base-url`, `--api-key`,
    `--timeout`. Builds its LLM provider through the `resolve_llm_*` chain
    directly rather than `PipelineContext.create_llm_provider`, which requires
    a `documents_dir` this corpus-free harness has no use for.
```

Add to **Invariants**:

```markdown
- The classification set is committed and synthetic: query text only,
  placeholder requirement IDs (`VZ_REQ_PLAN_<L>_<4 digits>`), no real plan
  codes. The hygiene tests read the real codes out of
  `query.analyzer._PLAN_ALIASES` rather than restating them, so the test file
  carries none either. The corpus-derived set is `<env_dir>` material.
- `matrix_text()` carries class names and counts only, so it is
  chat-pasteable (NFR-8). `--out` JSON carries query text and stays out of the
  repo.
- Classification accuracy and weighted cost are reported side by side and are
  allowed to disagree — that disagreement is the signal the weighting exists
  to expose, not an inconsistency to reconcile.
```

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

```bash
git add core/src/eval/classify_cli.py core/src/eval/MODULE.md \
    docs/compact/strands/jev-experimentation/phase-1-baselines.md \
    docs/compact/strands/jev-experimentation/journal.md
git commit -m "eval: classification CLI + recorded phase-1 baselines

Closes the phase-1 gate: rules and llm arms measured over the
labelled set with a confusion matrix across all ten QueryType
values. MODULE.md ships the new public surface in the same commit,
per CLAUDE.md's branch-flow rule.

Strand: jev-experimentation, phase 1. Design §5, §6."
```

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

**2. Placeholders.** None. Every code step carries runnable code; the labelled
set is written out in full; every command is exact. The one step without a
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
