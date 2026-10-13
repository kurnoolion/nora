# Decision-model design — typed classification at the query-analyzer seam

Strand: `jev-experimentation` · Draft v0.1, 2026-10-13 · Status: draft, for review
(v0.1: first design. Seam partition by output shape, `/v1/systemone` as the
internal wire format, Kev as iteration-1 candidate, four-arm measurement with
per-class confusion matrix, five gated phases mapping the architect's
directive.)

Inputs: the architect's directive of 2026-10-13 (six phases, recorded verbatim
in `journal.md`); `decisions-draft.md` D-DRAFT-1..10; `corp-db-layer`'s
`storage-design.md` as the pattern for flag-gated phased migration with a
legacy default and an identity gate; the landed `llm-roster-deploy` /
`llm-model-discovery` work as the roster precedent; architect-confirmed
constraints — no external egress from build/serve, Ollama **and** vLLM both in
production, DGX Spark committed as LLM-synthesis backup, A4000 (16 GB) the
practical host.

## 1. Shape of the answer

NORA's query pipeline opens with `QueryAnalyzer.analyze()`, one LLM call that
both classifies the query and extracts entities from it. Everything downstream
is routed by its `query_type`. This design replaces **only the classification
half** with a typed decision model, leaves the extraction half on the regex
paths that already exist, and puts the whole thing behind a flag with legacy
as the default.

Three properties make the seam unusually cheap to change:

- `make_query_analyzer()` is already a factory — one function selects the
  implementation.
- `QueryIntent` is the contract, and it does not change. Nothing downstream
  learns that anything happened.
- A rule-based implementation (`MockQueryAnalyzer`) already exists and already
  implements every extractor, so the non-decision half of the work is written
  and tested.

The objective is capability and recorded expertise, not a benchmark win
(D-DRAFT-6). "The rule-based baseline won" is a successful outcome of this
strand, provided it is measured and recorded.

## 1a. The picture — the analyzer seam and the decision path

```
                          query text
                               │
                 ┌─────────────┴──────────────┐
                 │  make_query_analyzer()     │   flag + roster
                 └─────────────┬──────────────┘
          ┌──────────────┬─────┴──────┬──────────────────┐
          ▼              ▼            ▼                  ▼
   MockQuery        LLMQuery     HybridQuery        (future arms)
   Analyzer         Analyzer      Analyzer
   (rules)          (legacy       ┌──┴────────────────┐
                     DEFAULT)     │ DecisionProvider  │ ── /v1/systemone
                                  │  query_type       │     (vLLM, Kev)
                                  │  doc_type_scope   │
                                  │  mnos / features  │  parallel bools
                                  │  / plan_ids       │
                                  └──┬────────────────┘
                                     │ composed with
                                  ┌──┴────────────────┐
                                  │ regex extractors  │ (reused from
                                  │  entities         │  MockQueryAnalyzer)
                                  │  concepts         │
                                  │  releases         │
                                  │  standards_refs   │
                                  └──┬────────────────┘
                                     ▼
                              QueryIntent  (contract unchanged)
                                     │
        ┌────────────────────────────┴───────────────────────────┐
        │  every downstream knob reads intent.query_type:         │
        │  rewrite gate (pipeline.py:500) · top_k · BM25 weight   │
        │  · rerank enable · distance threshold · grouping        │
        │  · synthesis system prompt                              │
        └─────────────────────────────────────────────────────────┘
```

Classification sits at the head of the interactive path and **must** stay
upstream of query expansion: `pipeline.py:500` gates expansion on
`_TYPE_REWRITE_ENABLED.get(intent.query_type, False)`, so expansion is a
consequence of classification, not a peer stage (D-DRAFT-8). This answers
directive phase 3's before-or-after question from the code rather than from
preference.

## 2. What the decision model owns — `QueryIntent` partitioned by output shape

`QueryIntent` (`core/src/query/schema.py:62`) carries nine populated fields. A
decision model emits only `bool` / `score` / `choice`, so it cannot be a
drop-in replacement for the method. The partition follows the data
(D-DRAFT-7):

| Field | Shape | Owner |
|---|---|---|
| `query_type` | `choice`, 10 options | decision model |
| `doc_type_scope` | `choice`, 3 options | decision model |
| `mnos` | closed vocab (VZW/TMO/ATT) | decision model — parallel `bool` per candidate |
| `likely_features` | closed vocab (`_FEATURE_KEYWORDS` keys) | decision model — parallel `bool` per candidate |
| `plan_ids` | closed vocab (5 values) | decision model — parallel `bool` per candidate |
| `entities` | free-text spans | regex extractor |
| `concepts` | free-text spans | regex extractor |
| `releases` | free-text spans | regex extractor |
| `standards_refs` | free-text spans | regex extractor |

The three closed-vocabulary multi-selects decompose into one boolean question
per candidate, evaluated against a single shared `state` (the query) in one
request — which is the pattern the category is built around, and sits far
inside a 64k state / 32k question budget for a single query.

The open-extraction fields have no option set, so no decision model can
produce them. They stay on `MockQueryAnalyzer`'s existing regex extractors.

**Phase 1 scope note:** `query_type` + `doc_type_scope` only. The three
multi-selects are deliberately deferred to phase 3 so the first measurement
has one variable.

### 2.1 A defect this fixes by construction

`QueryType` has ten members. `LLMQueryAnalyzer`'s prompt enumerates eight and
omits `SUMMARIZE` and `FACT`, so under the LLM analyzer those two classes are
unreachable and their per-type knobs are dead — wide `top_k` + grouping
disabled for `SUMMARIZE`, tight `top_k` + strict `max_distance_threshold` +
rerank on for `FACT`. `MockQueryAnalyzer` emits both (`analyzer.py:269`,
`:283`), so the two analyzers disagree on the available class set.

A typed option set is a schema, not prose inside a prompt, so this failure
mode cannot recur on the decision path. **The defect must be fixed before any
legacy-vs-decision comparison runs**, or the legacy arm is measured against
broken code and the class-distribution differences will be misread as a model
effect.

## 3. The decision seam in code

A `DecisionProvider` protocol speaking `/v1/systemone` — typed `bool` /
`score` / `choice` questions against one shared `state` (D-DRAFT-9). Direct
precedent: `OpenAICompatibleProvider` speaks `/v1/chat/completions` over
stdlib urllib with no SDK dependency, which is what made the LLM roster
vendor-neutral and let `llm-model-discovery` add model selection without
touching providers.

Adopting the de-facto decision-model wire format means candidate swaps are a
config change, and if hosted Jev ever became reachable (it is not —
D-DRAFT-1) it is the same interface with no rework.

Proposed placement: a new `core/src/decision/` package — a 17th package with
its own `MODULE.md`, rather than growing `core/src/llm/`. It is a different
abstraction, and one-package-per-concern is the convention here. Candidates
without a `/v1/systemone` endpoint (ModernBERT-class; constrained decoding
over a resident model) implement the protocol as a thin in-process adapter
instead of an HTTP hop.

Configuration follows the roster precedent exactly: a flag selects the path,
a roster selects the provider, legacy is the default, nothing changes for an
operator who sets neither.

## 4. Candidates and hardware

Verified first-hand; earlier secondary listings are not load-bearing.

| Candidate | Licence | `/v1/systemone` | Fits A4000 16 GB | Disposition |
|---|---|---|---|---|
| **Kev** 0.8B / 4B | Qwen3.5 bases Apache-2.0 | native | yes | **iteration 1** |
| openjev (DiffusionGemma 26B-A4B) | Apache-2.0 | native + `/v1/decisions` | no — 24 GB+ | parked |
| GEV-26B-Decide | Apache-2.0 | — | no — 24 GB+ even at NVFP4 (17.1 GiB) | parked |

Kev checkpoints are a rank-16 LoRA adapter plus a small bilinear pointer head
on a Qwen3.5 base; the readout scores each option's hidden state against the
question's `<decide>` hidden state and softmaxes (D-DRAFT-10).

**Kev's trainability is the decisive property.** Published counter-evidence
holds that small purpose-trained classifiers beat generic decision models on
trained tasks (a 200M DeBERTa at 89.01% / 54 ms against hosted Jev's 86.35% /
348 ms on prompt injection). Because Kev is LoRA-trainable, a Kev fine-tuned
on NORA's labelled queries *is* that purpose-trained classifier — with
calibrated typed outputs and the decision interface. The baseline arm and the
decision arm merge in iteration 2 rather than competing.

Serving: decision path on vLLM, legacy LLM path stays on Ollama. The arms do
not contend, and the GGUF ban (D-DRAFT-3) is satisfied by construction rather
than by discipline.

**Prior art warning carried into this design:** NORA already ships a
head-bearing model — `CrossEncoderReranker` (`reranker.py:90`,
`ms-marco-MiniLM-L6-v2`), same architecture class with a scoring head. The
A11 result had it net-zero and slightly regressive (86.4% / 62.3%), diagnosed
as out-of-distribution on telecom spec text. A generic Kev should be expected
to hit the same wall for the same reason. This is the strongest argument for
budgeting the fine-tuning step rather than treating it as optional.

## 5. Migration path — five gated phases

Maps the architect's six directive phases onto `corp-db-layer`'s
flag-gated-with-legacy-default template. Terminology, used consistently
below: a **phase** is one gate of this strand's plan; **iteration 1** is
phases 1-5 taken together, and **iteration 2** is follow-on work — the LoRA
fine-tune and the remaining six LLM decision points — which may land in a
successor strand. Each phase is additive; today's
behaviour is the default throughout.

- **Phase 1 — characterize + instrument.** Fix the `SUMMARIZE`/`FACT` prompt
  defect (§2.1). Build the labelled query set (§6). Measure the two existing
  arms — `MockQueryAnalyzer` and `LLMQueryAnalyzer` — to establish baselines.
  No new model, no new dependency. **Gate:** baselines recorded with a
  confusion matrix over all 10 classes.
- **Phase 2 — the seam.** `core/src/decision/` with the `DecisionProvider`
  protocol, a `HybridQueryAnalyzer`, the flag, the roster, and a mock
  provider. **Gate:** full suite green; with the flag off, behaviour is
  byte-identical to today (identity gate, per `corp-db-layer`'s precedent).
- **Phase 3 — first real provider.** Kev on vLLM behind the roster. Assert the
  decision heads are actually loaded before any accuracy number is recorded —
  per D-DRAFT-3 a silent degradation to base-model generation would invalidate
  the comparison without erroring. Optionally extend to the three
  closed-vocabulary multi-selects. **Gate:** four-arm comparison complete.
- **Phase 4 — trial stack.** Stack on new ports with the flag enabled, A/B
  against legacy, per the directive. **Gate:** team-usage round.
- **Phase 5 — default flip.** Rebuild the production image with the flag
  default-on **only if** the measurement supports it. If it does not, the
  strand lands with the flag default-off and the findings recorded; that is a
  completed strand, not an abandoned one (D-DRAFT-6).

## 6. Measurement

Four arms: `MockQueryAnalyzer` (rules) · `LLMQueryAnalyzer` (legacy, defect
fixed) · Kev generic · Kev fine-tuned on NORA labels — the fourth arm
conditional on §8 open point 3, since the fine-tune may belong to a successor
strand. The first three are unconditional.

**Required output is a per-class confusion matrix over all 10 `QueryType`
values**, not an overall accuracy figure (D-DRAFT-8). Because classification
sits at the head of the pipeline, a misclassification propagates into every
downstream knob. Worked example: a candidate at 90% overall that
systematically confuses `FACT` with `SUMMARIZE` inverts two opposed retrieval
configurations — tight `top_k` + strict threshold + rerank on, versus wide
`top_k` + grouping off — and could be worse in practice than the rule-based
baseline.

Also recorded per arm: determinism across repeated runs on identical input,
and latency (in series with the whole pipeline, so it is user-visible).

**The labelled set is the instrument and it does not exist yet.** ~40-60
queries spanning all 10 classes, each labelled with expected `QueryType` +
`DocTypeScope`. The frozen 39-sample golden set (digest `29088f2a4167`) could
not separate bm25x from fts5 in the `corp-db-layer` engine A/B and will not
separate a decider from an LLM either — without new labels this experiment can
only return an underpowered null. Candidate label sources: recorded user
questions from the `ask-history` submit-time recording, the 18-Q OA eval set,
synthetic queries for thin classes.

Reporting must keep three questions separate, because they will be conflated
otherwise: does the decision path **work**, is it **faster**, is it **more
accurate**. Legitimate answers are yes / yes / no.

## 7. Invariants

- **Legacy is the default** at every phase. An operator who sets no flag sees
  today's behaviour, byte-identical.
- **No external egress.** Every candidate is self-hosted in-corp
  (D-DRAFT-1). No decision-model traffic leaves the network.
- **No proprietary corpus content** in logs, reports, or test fixtures — the
  standing project rule. Note that the phase-1 target sees only query text,
  not corpus content; the other six LLM decision points do not share that
  property and each needs its own assessment before being touched.
- **GGUF is banned** for head-bearing decision models (D-DRAFT-3). vLLM only.
- **Decision heads must be asserted loaded** before any accuracy number is
  recorded.
- `QueryIntent` is the contract and does not change.

## 8. Open points for adjudication

1. **Module placement** — `core/src/decision/` as a 17th package (proposed)
   versus extending `core/src/llm/`.
2. **Canonical Kev upstream.** Many near-identical forks exist
   (`jaredpalmer`, `chi2liu`, `song007`, `Radexito`, others) with no clear
   provenance ordering. Iteration 1 brings external model code and weights
   into the corporate network; this needs whoever owns software intake to
   rule, before anything is pulled.
3. **Fine-tuning budget.** §4's prior-art warning argues the generic-Kev arm
   is likely to underperform for the same OOD reason the cross-encoder did.
   Is the LoRA fine-tune in scope for this strand, or a successor?
4. **Who owns the `SUMMARIZE`/`FACT` defect fix** (§2.1) — this strand, or a
   separate bugfix branch? It is independent of the decision-model work and
   blocks the phase-1 gate.
5. **Architect questions still open:** production image/stack name for the
   phase-5 rebuild; exact DGX Spark specification.
6. **Phase-5 criteria.** What measurement result justifies flipping the
   default? Stating it now prevents the decision being relitigated later
   against a moving bar.
