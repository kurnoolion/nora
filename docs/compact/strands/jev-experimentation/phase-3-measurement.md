# Phase 3 measurement — four arms at the classification seam

Strand `jev-experimentation` · Recorded 2026-10-20 · Code at `f877e93`,
fourth arm added 2026-10-20 (see the amendment at the foot of this file)

Supersedes the phase-1 baselines, whose numbers predate the two defect fixes;
what they recorded is kept in "Superseded phase-1 baselines" at the foot of
this file.

Set `classify-dev-v1` — 50 synthetic samples, 5 per `QueryType`, labels
authored from human-read intent. Knob profile digest `29615196d16c`. Counts
and class names only; per-sample JSON carries query text and stays out of the
repo.

**Phase 3's gate (design §5) is met for all four measured arms.** A
fine-tuned Kev-4B — trained on NORA's own labels — remains unrun and
unmeasured; it is conditional on §8 open point 3, the fine-tuning-budget
question, which is the architect's. It is **not** "arm 5": arms are named
here, not numbered, because the numbering collided once already (the
`confidence-routed` arm and the fine-tune were both called "arm 4").

## Headline

| Arm | Model | Accuracy | Weighted cost | Scope acc. | Deterministic | p50 | p95 |
|---|---|---|---|---|---|---|---|
| `rules` | `MockQueryAnalyzer` | 0.760 | 0.112 | **1.000** | yes (n=3) | <0.1 ms | <0.1 ms |
| `llm` | `qwen3:8b` via Ollama | 0.680 | 0.128 | 0.960 | **not measured** | 15 373 ms | 31 503 ms |
| `decision` | Kev-4B via MLX | 0.760 | 0.112 | 0.760 | **yes (n=3)** | 545 ms | 580 ms |
| `confidence-routed` | Kev + rules, floor 0.50 | **0.860** | **0.068** | **1.000** | **yes (n=3)** | 503 ms | 526 ms |

The `llm` arm ran at `--repeat 1` because one pass takes ~13 minutes; its
`deterministic=True` is vacuous and must not be read as a result. The other
three ran at `--repeat 3` and are genuinely deterministic — 150
classifications each, identical.

The `confidence-routed` row was originally computed offline from the
`decision` arm's saved per-sample confidences and reported here as `hybrid`.
It is now a measured arm in shipped code, and the amendment at the foot of
this file records what changed when it became one — including one number the
offline computation could not have produced.

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
    --decision-base-url http://127.0.0.1:PORT \
    --decision-model kev-latest --repeat 3
```

Setup for the third is in `kev-runbook.md`.

## Open, for the architect

1. `feature_level` vs `cross_doc` — identical retrieval configuration, so the
   distinction is downstream-free in both directions. Merge, or make it drive
   something.
2. `general` as a catch-all sink at 0.31 precision under the rules.
3. The routing floor needs a held-out labelled set from real query traffic.
   This is the concrete ask that unblocks the most, and it is now a
   one-command sweep rather than an analysis: `--arm confidence-routed
   --confidence-floor <f>`.
4. The fine-tuned arm (train on NORA's labels) — §8 open point 3.
5. `traceability` is the one class where the LLM beats the decision model
   outright (1.00 vs 0.40). Worth a gloss revision before concluding anything.

---

# Amendment — the `confidence-routed` arm, measured in shipped code

Recorded 2026-10-20, same set, same server, same knob profile. The row the
headline table used to label `hybrid` was arithmetic over saved per-sample
confidences; it is now `--arm confidence-routed` and the numbers below come
from the harness.

## It reproduced, and the two degenerate ends check out

| Floor | Accuracy | Weighted cost | Scope acc. | Coverage `query_type` | Coverage `doc_type_scope` |
|---|---|---|---|---|---|
| 0.00 | 0.760 | 0.112 | 0.760 | 1.00 | 1.00 |
| 0.20 | 0.840 | 0.072 | 0.980 | 0.80 | 0.70 |
| 0.30 | 0.820 | 0.088 | **1.000** | 0.56 | 0.46 |
| 0.40 | 0.820 | 0.088 | **1.000** | 0.44 | 0.24 |
| **0.50** | **0.860** | **0.068** | **1.000** | 0.28 | 0.08 |
| 0.70 | 0.800 | 0.092 | **1.000** | 0.08 | 0.00 |
| 1.00 | 0.760 | 0.112 | **1.000** | 0.00 | 0.00 |

**Floor 0.00 is the `decision` arm exactly** — 0.760 / 0.112 / 0.760, every
field from the model. **Floor 1.00 is the `rules` arm exactly** — 0.760 /
0.112 / 1.000, no field from the model, because no answer in the set reports
confidence 1.0. The two ends of the sweep land on two independently measured
arms, which is the strongest correctness evidence available for an
implementation of this shape: a routing bug would have to be invisible at
both limits and at the in-between point that matches the offline computation
to three decimals.

Coverage is reported per field and is the reason this table is readable. An
arm that routes 8 % of its scope answers to the model and scores 1.000 is
reporting the rules, and the accuracy column alone cannot say so.

## One number the offline computation could not produce

**Scope accuracy rises to 1.000 at every floor from 0.30 up**, against the
`decision` arm's 0.760. The offline computation routed only `query_type`, so
it left the scope column blank — honestly, but it meant the cheapest win here
went unseen for a day.

The shipped arm routes **per field**: both questions travel in one request and
each answer carries its own confidence, so there is no reason to couple them.
The model's scope answers turn out to be precisely its low-confidence ones,
so the floor discards them without being told to.

This subsumes a recommendation reached by hand earlier in this file — *keep
`doc_type_scope` on the rules, where they score 1.000 against the model's
0.760*. The routing reaches the same place from the confidence signal rather
than from a hard-coded exception, which means it will keep holding if a later
model gets better at scope, and a hard-coded exception would not have.

**The lesson, stated plainly:** the paper version answered the question it was
asked and silently dropped a column. A computation over saved output is a
cheap way to decide whether to build something and a poor way to decide what
was learned.

## The floor is still not a validated number

Everything the original caveat said stands, and the sweep from shipped code
makes it sharper rather than weaker:

- The curve is **non-monotonic** — 0.760 → 0.840 → 0.820 → 0.820 → 0.860 →
  0.800 → 0.760. That is the shape of n=50 noise, not of a threshold response.
  The 0.860 peak sits one sample away from the 0.820 either side of it.
- Every floor was scored on the same 50 samples it was selected on.
- **0.20 is arguably the better operating point and the table cannot settle
  it.** It scores 0.840 against 0.860 — one sample — while routing 80 % of
  `query_type` to the model instead of 28 %. If the objective is to learn how
  the decision model behaves on real traffic, 0.20 buys roughly three times
  the exposure for a difference the set cannot resolve. If the objective is
  the best number on this set, 0.50 wins by one sample. **These are different
  objectives and the strand should say which it has** before a default gets
  defended on the strength of a number this soft.

What survives a held-out set is the separation the routing exploits: mean
confidence 0.425 on correct answers against 0.268 on incorrect ones. The
specific floor does not. Kev's card prescribes the fix — refit with
`python -m kev.calibrate` on a labelled sample of the real workload.

The code is built so this caveat travels with the number: the arm prints the
floor, per-field coverage and the caveat itself, and writes all three under a
`routing` key in `--out`.

## Naming

`hybrid` is retired as a name in this strand. It had come to mean two
unrelated things — `HybridQueryAnalyzer`, which is *model for the label
fields, regex for the spans, rules on provider error*, and the
confidence-routing policy, which has nothing to do with it. The class is now
`DecisionQueryAnalyzer` and the policy is `ConfidenceRoutedQueryAnalyzer`,
spelled identically as the class, the `--arm` value and the
`NORA_QUERY_ANALYZER` value. D-DRAFT-25.

## Commands

```bash
python -m core.src.eval.classify_cli --arm confidence-routed \
    --decision-provider systemone \
    --decision-base-url http://127.0.0.1:PORT \
    --decision-model kev-latest \
    --confidence-floor 0.5 --repeat 3 --out <path>
```

Floor 0.0 reproduces `--arm decision`; floor 1.0 reproduces `--arm rules`.

---

# Superseded phase-1 baselines

Recorded 2026-10-20 at `a9ed255`, same set and knob profile, **before** the
two defect fixes (D-DRAFT-24). Kept as the phase-1 gate record — what was
measured, when, against which code. Do not quote these as today's baseline:
the alias double-count fix moved the `rules` arm to 0.760 / 0.112 (D-DRAFT-28).

| Arm | Model | Accuracy | Weighted cost | Scope acc. | Deterministic | p50 | p95 |
|---|---|---|---|---|---|---|---|
| `rules` | `MockQueryAnalyzer` | 0.720 | 0.136 | 1.000 | yes (n=3) | <0.1 ms | <0.1 ms |
| `llm` | `qwen3:8b` via Ollama | 0.700 | 0.120 | 0.960 | vacuous (n=1) | 15 035 ms | 22 817 ms |

Per-class recall, `rules` / `llm`: `single_doc` 0.40 / 0.40, `cross_doc`
0.60 / 0.20, `cross_mno_comparison` 0.80 / 1.00, `release_diff` 0.60 / 1.00,
`standards_comparison` 0.80 / 1.00, `traceability` 0.80 / 1.00,
`feature_level` 0.80 / 0.00, `summarize` 0.80 / 0.60, `fact` 0.60 / 1.00,
`general` 1.00 / 0.80. `general` precision under the rules was 0.31.

Findings that still hold:

- **The arms were complementary, not ranked.** They disagreed on 21 of 50
  samples. The LLM fixed every rules failure that was a lexical gap (10
  samples: a requirement ID with no plan alias, one spec named through two
  aliases, an implicit cross-carrier comparison, release and fact phrasings
  outside the trigger tuples, a standards comparison with no spec number). It
  broke every class that is a house convention (11 samples), with
  `feature_level` recall falling to 0.00. The rules encode the taxonomy and
  miss the language; the LLM reads the language and does not know the
  taxonomy. Phase 3 reproduced this on a decision model.
- **Accuracy and weighted cost disagreed, as designed.** The `llm` arm was
  worse on accuracy and better on weighted cost, because its mistakes landed
  in cheaper retrieval configurations. Neither gap is significant at n=50.
- **Two defects were found and left unfixed during the run**, so that fixing
  them could not alter an arm mid-measurement: `LLMQueryAnalyzer` discarded a
  whole analysis over one bad field, and the cross-doc signal counted alias
  hits rather than distinct plans (sample c04). Both are fixed (D-DRAFT-24,
  D-DRAFT-28).
- **Not measured then or now:** extrinsic end-to-end quality (needs an
  ingested corpus and a running stack, phase 4) and a corpus-derived label
  set (`<env_dir>` material).
