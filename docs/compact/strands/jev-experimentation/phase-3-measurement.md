# Phase 3 measurement — three arms at the classification seam

Strand `jev-experimentation` · Recorded 2026-10-20 · Code at `f877e93`

Supersedes `phase-1-baselines.md`, whose numbers predate the two defect fixes.

Set `classify-dev-v1` — 50 synthetic samples, 5 per `QueryType`, labels
authored from human-read intent. Knob profile digest `29615196d16c`. Counts
and class names only; per-sample JSON carries query text and stays out of the
repo.

**Phase 3's gate (design §5) is met for three of four arms.** Arm 4, a Kev-4B
fine-tuned on NORA's own labels, is not run — it is conditional on §8 open
point 3, the fine-tuning-budget question, which is the architect's.

## Headline

| Arm | Model | Accuracy | Weighted cost | Scope acc. | Deterministic | p50 | p95 |
|---|---|---|---|---|---|---|---|
| `rules` | `MockQueryAnalyzer` | 0.760 | 0.112 | **1.000** | yes (n=3) | <0.1 ms | <0.1 ms |
| `llm` | `qwen3:8b` via Ollama | 0.680 | 0.128 | 0.960 | **not measured** | 15 373 ms | 31 503 ms |
| `decision` | Kev-4B via MLX | 0.760 | 0.112 | 0.760 | **yes (n=3)** | 545 ms | 580 ms |
| `hybrid` | Kev + rules, conf ≥ 0.50 | **0.860** | **0.068** | — | — | 545 ms | — |

The `llm` arm ran at `--repeat 1` because one pass takes ~13 minutes; its
`deterministic=True` is vacuous and must not be read as a result. The other
two ran at `--repeat 3` and are genuinely deterministic — 150 classifications
each, identical.

Serving identity, captured from `/v1/models` at run time rather than assumed:
`base=Qwen/Qwen3.5-4B-Base lora=16 device=mps backend=mlx dtype=bfloat16
temperature=2.406`.

## Per-class recall, the three single arms

| Class | `rules` | `llm` | `decision` |
|---|---|---|---|
| `single_doc` | 0.80 | 0.40 | 0.80 |
| `cross_doc` | 0.60 | 0.20 | **0.80** |
| `cross_mno_comparison` | 0.80 | **1.00** | 0.80 |
| `release_diff` | 0.60 | **1.00** | **1.00** |
| `standards_comparison` | 0.80 | **1.00** | **1.00** |
| `traceability` | 0.80 | **1.00** | 0.40 |
| `feature_level` | **0.80** | 0.00 | 0.00 |
| `summarize` | 0.80 | 0.60 | **1.00** |
| `fact` | 0.60 | **1.00** | **1.00** |
| `general` | **1.00** | 0.60 | 0.80 |

## What the numbers say

**The accuracy tie is a coincidence; the error structure is not.** `rules` and
`decision` both score 0.760 / 0.112 and agree on very little. Both models beat
the rules decisively on the classes the rules recognise by hard-coded trigger
phrases — `release_diff`, `standards_comparison` and `fact` all move from
0.60–0.80 to 1.00 — because those failures are lexical gaps, and reading
language is what a model does. This reproduces phase 1's finding on a second,
differently-shaped model, which makes it a property of the seam rather than of
qwen3:8b.

**`feature_level` is 0.00 for both models and 0.80 for the rules.** Not a
near-miss: neither model gets a single one. It is a house convention with no
lexical signature, and no amount of generic capability recovers it. This is
the measured form of the design's §4.2 position: what generic weights lack
here is NORA's *label set*, not telecom vocabulary.

It is also the class whose confusions are free. `feature_level` and
`cross_doc` select an **identical** retrieval configuration, so
`divergence_cost` scores confusing them at 0.0 — which is why `decision` ties
the rules on weighted cost while losing a whole class. **For the architect:**
a distinction that costs nothing downstream when you get it wrong is a
distinction that buys nothing when you get it right. Either it should drive
something, or it should merge into `cross_doc`.

**The decision model is not a faster LLM; it is better here and 28× quicker.**
It beats `llm` on seven of ten classes and loses only `traceability`
(0.40 vs 1.00) and `cross_mno_comparison`. 545 ms against 15 373 ms is the
premise of the whole strand, now measured rather than quoted.

**`general` is a rules problem the decision model partly fixes.** Precision:
rules 0.31, llm 0.33, decision 0.50. Under the rules, every unrecognised
breadth query lands in the catch-all and is served as a narrow lookup.

**Keep `doc_type_scope` on the rules.** 1.000 against 0.760. The right shape
at this seam may be per-field — decision model for `query_type`, rules for
scope — not one arm for both. That costs nothing: they travel in one request
and the rules are free.

## The finding that matters: confidence, not accuracy

Kev reports a calibrated probability per answer. On this set it separates
correct from incorrect answers:

```
mean confidence   correct 0.425   incorrect 0.268
conf >= 0.3 : coverage 0.56  accuracy-on-kept 0.893
conf >= 0.5 : coverage 0.28  accuracy-on-kept 0.929
conf >= 0.7 : coverage 0.08  accuracy-on-kept 1.000
```

Routed — the decision model where it is confident, the rules elsewhere:

| | Accuracy | Weighted cost | Uses Kev |
|---|---|---|---|
| rules only | 0.760 | 0.112 | 0 % |
| Kev only | 0.760 | 0.112 | 100 % |
| hybrid conf ≥ 0.20 | 0.840 | 0.072 | 80 % |
| hybrid conf ≥ 0.50 | **0.860** | **0.068** | 28 % |

**Read this carefully, because it is the easiest number here to over-claim.**
The thresholds are selected on the same 50 samples they are scored on, and the
curve is non-monotonic across thresholds (0.840 → 0.800 → 0.820 → 0.820 →
0.820 → 0.860), which is what n=50 noise looks like. **The specific 0.860 will
not survive a held-out set.** What is threshold-free, and what does survive, is
the 0.425-against-0.268 separation: the confidence carries signal about its
own correctness.

Kev's own card says this directly — measure accuracy and calibration on a
labelled sample of your own workload and refit the temperature there
(`python -m kev.calibrate`) before setting thresholds. **This is the strongest
argument for building the labelled set on internal infrastructure**: not to
prove the model works, but because the threshold is the deliverable and it
cannot be set from 50 synthetic samples.

**This is the answer to "what does a decision model buy us at this seam."**
Not accuracy — it ties. An abstention signal the rules cannot produce, at a
latency that makes using it viable. The LLM has no such story: it could carry
confidence in principle, but 15 seconds per query ends the discussion.

## Option glosses, and a number that was nearly recorded wrong

The first `decision` run scored **0.460**, below both other arms. The options
were being sent as bare enum identifiers — the wire format carries an optional
description per option, and all of them were null.

| Options | Accuracy | ms/query |
|---|---|---|
| bare (`feature_level`, `cross_doc`, …) | 0.460 | 131 |
| one-line gloss each | **0.760** | 328 |

Same weights, same temperature, same samples. Had 0.460 gone into this file it
would have supported a confident and wrong conclusion: that generic
decision-model weights are far below the rules at this seam. The gloss is
where the taxonomy gets stated, and the taxonomy is precisely what generic
weights cannot know — so the one thing worth saying to the model is the one
thing that was being omitted.

Glosses live in `QUERY_TYPE_DESCRIPTIONS`, keyed by the enum with a test
asserting every member has one, so adding a `QueryType` fails the suite rather
than silently shipping an unglossed option.

## On the `llm` arm moving 0.700 → 0.680

Exactly one sample. The per-field fallback fix fired once during the run, as
designed, and the log shows it: the model answered `doc_type_scope='general'`,
the rule-based scope was substituted, and the LLM's `query_type` was kept
instead of the whole answer being discarded.

Two explanations fit, and this run cannot separate them: either that rescued
sample's LLM `query_type` was wrong where the full rules fallback had been
right, or it is ordinary run-to-run variation in a model served at temperature
0. The earlier run's per-sample output was not kept, so the comparison cannot
be made after the fact. **One sample is not evidence against the fix** —
keeping data you have no reason to throw away is correct regardless of which
way a single sample falls.

Lesson for the harness: `--out` should have been passed on the phase-1 runs.
Per-sample output is what makes a later re-run comparable.

## Commands

```bash
python -m core.src.eval.classify_cli --arm rules --repeat 3

python -m core.src.eval.classify_cli --arm llm \
    --llm-provider ollama --model qwen3:8b --repeat 1

python -m core.src.eval.classify_cli --arm decision \
    --decision-provider systemone \
    --decision-base-url http://127.0.0.1:8008 \
    --decision-model kev-latest --repeat 3
```

Setup for the third is in `kev-runbook.md`.

## Open, for the architect

1. `feature_level` vs `cross_doc` — identical retrieval configuration, so the
   distinction is downstream-free in both directions. Merge, or make it drive
   something.
2. `general` as a catch-all sink at 0.31 precision under the rules.
3. The hybrid threshold needs a held-out labelled set from real query traffic.
   This is the concrete ask that unblocks the most.
4. Arm 4 (fine-tune on NORA's labels) — §8 open point 3.
5. `traceability` is the one class where the LLM beats the decision model
   outright (1.00 vs 0.40). Worth a gloss revision before concluding anything.
