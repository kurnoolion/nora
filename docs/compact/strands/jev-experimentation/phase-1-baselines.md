# Phase 1 baselines — query classification

Strand: `jev-experimentation` · Recorded 2026-10-20 · Code at `a9ed255`

Closes the phase-1 gate (design §5): "baselines recorded with a confusion
matrix over all 10 classes."

Set `classify-dev-v1` — 50 synthetic samples, 5 per `QueryType`, labels
authored from human-read intent (`core/src/eval/classify_set.json`).
Knob profile digest `29615196d16c`. Counts and class names only; the
per-sample JSON holds query text and stays out of the repo.

> **Superseded on 2026-10-20 by `phase-3-measurement.md`.** Both numbers
> below were measured before the two defects found during this run were
> fixed, and the alias double-count fix moves the `rules` arm to 0.760
> accuracy / 0.112 weighted cost (two samples, c02 and c04, both wrong →
> right). This file is kept as the phase-1 gate record — what was measured,
> when, against which code — not as the current standing numbers. Do not
> quote the table below as today's baseline.

## Headline

| Arm | Model | Accuracy | Weighted cost | Scope acc. | Deterministic | p50 | p95 |
|---|---|---|---|---|---|---|---|
| `rules` | `MockQueryAnalyzer` | **0.720** | 0.136 | 1.000 | yes | <0.1 ms | <0.1 ms |
| `llm` | `qwen3:8b` via Ollama | 0.700 | **0.120** | 0.960 | yes (n=1) | 15 035 ms | 22 817 ms |

Commands:

```
python -m core.src.eval.classify_cli --arm rules --repeat 3
python -m core.src.eval.classify_cli --arm llm --llm-provider ollama \
    --model qwen3:8b --repeat 1
```

The `llm` arm ran at `--repeat 1` because a single pass took 12 min 58 s;
its `deterministic=True` is therefore vacuous and must not be read as a
determinism result. The `rules` arm ran at `--repeat 3` and is genuinely
deterministic.

## The three questions, kept separate (design §6)

**Does it work?** Both arms work. One defect surfaced — see below.

**Is it faster?** The rules are faster by roughly five orders of magnitude.
15 s of added latency per query, in series with the whole pipeline, is not a
viable production classification path. This is the measured version of the
reason the architect is assumed to have abandoned an LLM classifier.

**Is it more accurate?** No, and the honest answer is more interesting than
the number. The two arms are **complementary, not ranked**.

## The arms disagree structurally, not marginally

Accuracy differs by one sample out of fifty. That near-tie is a coincidence,
and pooling it hides the real finding: the arms disagree on **21 of 50**
samples, with the disagreements falling into two clean groups.

**The LLM fixes every rules failure that is a lexical gap (10 samples).**

| Sample shape | rules | llm |
|---|---|---|
| requirement ID named, no plan alias | `general` | `single_doc` |
| one spec named through two of its aliases | `cross_doc` | `single_doc` |
| implicit cross-carrier ("stricter than the others") | `general` | `cross_mno_comparison` |
| release wording outside the trigger tuple (2 samples) | `general` | `release_diff` |
| "the 3GPP standard" with no spec number cited | `general` | `standards_comparison` |
| fact phrasings outside the trigger tuple (2 samples) | `general` | `fact` |

**The LLM breaks every class that is a house convention (11 samples).** All
four remaining `feature_level` samples collapse into `general`, `summarize`
and `traceability` — `feature_level` recall goes 0.80 → **0.00**. Two
`summarize`, two `cross_doc` and two `single_doc` samples go the same way.

So the rules encode NORA's taxonomy and miss the language; the LLM reads the
language and does not know the taxonomy. Neither is a ceiling on the other.

**This is the single most useful result in phase 1.** The gap the LLM shows is
the *unfamiliar label set*, not unfamiliar telecom vocabulary — the model
handled telecom phrasing fine and failed on NORA-internal category
boundaries. That is evidence for the position the design doc argues on prior
grounds in §4.2: domain continued-pre-training is not what this seam needs,
and a small task fine-tune on the label set is the arm that would actually
matter.

## Accuracy and weighted cost disagree, as designed

The `llm` arm is worse on accuracy (0.700 vs 0.720) and better on weighted
cost (0.120 vs 0.136): its mistakes land in cheaper retrieval configurations.
Design §6 built the weighting to expose exactly this, and the first
measurement produced it. Reporting either number alone would have given the
opposite ranking.

Neither gap is significant at n=50 — one sample is 0.02 accuracy. The right
reading is "indistinguishable overall, structurally very different", which is
also why the corpus-derived set matters before any conclusion is drawn.

## Confusion matrices

### `rules` — `MockQueryAnalyzer`, repeat 3

```
expected \ predicted  single_d cross_do cross_mn release_ standard traceabi feature_ summariz     fact  general
single_d                     2        2        0        0        0        0        0        0        0        1
cross_do                     0        3        0        0        0        0        0        0        0        2
cross_mn                     0        0        4        0        0        0        0        0        0        1
release_                     0        0        0        3        0        0        0        0        0        2
standard                     0        0        0        0        4        0        0        0        0        1
traceabi                     0        0        0        0        0        4        0        0        0        1
feature_                     0        0        0        0        0        0        4        0        0        1
summariz                     1        0        0        0        0        0        0        4        0        0
fact                         0        0        0        0        0        0        0        0        3        2
general                      0        0        0        0        0        0        0        0        0        5

class                   support   recall  precision
single_d                      5     0.40       0.67
cross_do                      5     0.60       0.60
cross_mn                      5     0.80       1.00
release_                      5     0.60       1.00
standard                      5     0.80       1.00
traceabi                      5     0.80       1.00
feature_                      5     0.80       1.00
summariz                      5     0.80       1.00
fact                          5     0.60       1.00
general                       5     1.00       0.31
```

### `llm` — `qwen3:8b` via Ollama, repeat 1

```
expected \ predicted  single_d cross_do cross_mn release_ standard traceabi feature_ summariz     fact  general
single_d                     2        0        0        0        0        1        0        0        1        1
cross_do                     0        1        0        0        0        2        1        0        0        1
cross_mn                     0        0        5        0        0        0        0        0        0        0
release_                     0        0        0        5        0        0        0        0        0        0
standard                     0        0        0        0        5        0        0        0        0        0
traceabi                     0        0        0        0        0        5        0        0        0        0
feature_                     0        0        0        0        0        1        0        1        0        3
summariz                     1        0        0        0        0        0        0        3        0        1
fact                         0        0        0        0        0        0        0        0        5        0
general                      0        0        0        0        0        0        0        0        1        4

class                   support   recall  precision
single_d                      5     0.40       0.67
cross_do                      5     0.20       1.00
cross_mn                      5     1.00       1.00
release_                      5     1.00       1.00
standard                      5     1.00       1.00
traceabi                      5     1.00       0.56
feature_                      5     0.00       0.00
summariz                      5     0.60       0.75
fact                          5     1.00       0.71
general                       5     0.80       0.40
```

## Two defects found, neither fixed here

**1. `LLMQueryAnalyzer` discards a whole analysis over one bad field.**
The model answered `doc_type_scope` with `"general"` — not a `DocTypeScope`
member — and the `except (json.JSONDecodeError, ValueError, KeyError)` arm at
`core/src/query/analyzer.py` threw away the entire `QueryIntent`, including a
correct `query_type`, and fell back to keyword analysis. One sample of fifty
here, so the contamination of this baseline is small, but the behaviour is
structurally wrong: a per-field coercion that keeps the rule-based value for
the field that failed is strictly better. `HybridQueryAnalyzer._coerce`
(phase 2) already has that posture; the LLM analyzer should be brought into
line. Not changed here — it would alter the arm mid-measurement.

**2. The alias-count heuristic counts aliases, not distinct plans.**
`MockQueryAnalyzer._classify_query_type` sums `_PLAN_ALIASES` hits, so a
query naming one spec through two of its own aliases scores two signals and
routes to `cross_doc`. Sample c04. Found while authoring the set, deliberately
left alone so the baseline it anchors stays valid; it belongs in its own
change.

## What this does not measure

- **Extrinsic quality.** Design §6 requires end-to-end pipeline measurement
  alongside the intrinsic matrix, and the two can disagree. That needs an
  ingested corpus and a running stack, so it belongs with the phase-4 trial
  stack.
- **A corpus-derived label set.** `classify-dev-v1` is synthetic and written
  by one author in one sitting. The recorded-user-question set is `<env_dir>`
  material and is the instrument any production decision should rest on.
- **Any decision model.** Arms 3 and 4 are phase 3.
