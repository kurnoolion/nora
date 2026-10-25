# Decision-model design — typed classification at the query-analyzer seam

Strand: `jev-experimentation` · Draft v1.1, 2026-10-19 · Status: draft, for review
Line references are as of `fcc9fce` (origin/main, 2026-10-18). Identifiers are
stable; line numbers are not — re-anchor after any merge.
(v0.1: first design. v0.2: §2.1 blast radius verified against the per-type
maps — two features are entirely inert, not merely degraded; `_FEATURE_KEYWORDS`
counted at 16; factory signature read and the multi-mno-sira D-DRAFT-9 posture
cited; §2.1 defect fix claimed by this strand rather than left for adjudication.
v0.3: §4 split — 4.1 states that head-bearing IS the speed mechanism and that
every candidate including Kev is head-bearing; 4.2 scopes the prior-art
warning to accuracy of generic weights on domain text, with the A-series
identified as NORA's own eval runs.
v0.4: AutoTrust family enumerated with VRAM (none fits 16 GB); §4.0 Blocks of
Experts — frozen backbone, 40.2M detachable block, 0.5% of backbone; System 1
vs System 2 and adaptive thinking scoped out of phase 1; §7 GGUF detection made
concrete; openjev blocked on a licence conflict (§8.2).
v0.5: §8.2 expanded — NC is a purpose test, so internal corporate use is not
exempt; recommendation is to drop openjev rather than adjudicate it; Kev's
adapter/head licence flagged as distinct from its Apache-2.0 base.
v0.6: §4.3 added — AWQ via llm-compressor keeps the decision head in bf16
(precedent: JEV-27B-VL-AWQ), so JEV-9B plausibly reaches the 16 GB GPU host; AutoTrust
is parked on VRAM only, never on licence; roster is now two-horse. Seam partition by output shape, `/v1/systemone` as the
internal wire format, Kev as iteration-1 candidate, four-arm measurement with
per-class confusion matrix, five gated phases mapping the architect's
directive.)

Inputs: the architect's directive of 2026-10-13 (six phases, recorded verbatim
in `journal.md`); `decisions-draft.md` D-DRAFT-1..10; `corp-db-layer`'s
`storage-design.md` as the pattern for flag-gated phased migration with a
legacy default and an identity gate; the landed `llm-roster-deploy` /
`llm-model-discovery` work as the roster precedent; architect-confirmed
constraints — no external egress from build/serve, Ollama **and** vLLM both
available as serving runtimes, the large-memory host committed as LLM-synthesis
backup, a 16 GB on-prem GPU host the practical host.

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
        │  rewrite gate (pipeline.py:480) · top_k · BM25 weight   │
        │  · rerank enable · distance threshold · grouping        │
        │  · synthesis system prompt                              │
        └─────────────────────────────────────────────────────────┘
```

Classification sits at the head of the interactive path and **must** stay
upstream of query expansion: `pipeline.py:480` gates expansion on
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
| `mnos` | closed vocab (MNO-A/MNO-B/MNO-C) | decision model — parallel `bool` per candidate |
| `likely_features` | closed vocab (`_FEATURE_KEYWORDS` keys) | decision model — parallel `bool` per candidate |
| `plan_ids` | closed vocab (5 values) | decision model — parallel `bool` per candidate |
| `entities` | free-text spans | regex extractor |
| `concepts` | free-text spans | regex extractor |
| `releases` | free-text spans | regex extractor |
| `standards_refs` | free-text spans | regex extractor |

The three closed-vocabulary multi-selects decompose into one boolean question
per candidate, evaluated against a single shared `state` (the query) in one
request — which is the pattern the category is built around, and sits far
inside a 64k state / 32k question budget for a single query. Counted rather
than assumed: `_FEATURE_KEYWORDS` has **16** keys, so the full phase-3 shape is
16 + 3 + 5 = 24 booleans plus 2 choices = 26 questions against one state.
Comfortably inside the concurrency these servers document (openjev advertises
up to 64 concurrent reads).

The open-extraction fields have no option set, so no decision model can
produce them. They stay on `MockQueryAnalyzer`'s existing regex extractors.

**Phase 1 scope note:** `query_type` + `doc_type_scope` only. The three
multi-selects are deliberately deferred to phase 3 so the first measurement
has one variable.

### 2.1 A defect this fixes by construction

`QueryType` has ten members. `LLMQueryAnalyzer`'s prompt enumerates eight and
omits `SUMMARIZE` and `FACT`, so under the LLM analyzer those two classes are
unreachable. `MockQueryAnalyzer` emits both (`analyzer.py:279`, `:293`), so the
two analyzers disagree on the available class set.

Blast radius, verified against the per-type maps in `pipeline.py` rather than
assumed:

| Map | Its key set | Effect under `LLMQueryAnalyzer` |
|---|---|---|
| `_TYPE_MAX_DISTANCE` (`:105`) | `FACT: 0.4`, `SUMMARIZE: 0.7` — **only these two** | **entirely inert** |
| `_TYPE_DISABLE_GROUPING` (`:180`) | `SUMMARIZE` — **only this one** | **entirely inert** |
| `_TYPE_REWRITE_ENABLED` (`:132`) | 7 types incl. `SUMMARIZE` | loses the `SUMMARIZE` entry; other 6 unaffected |
| `_TYPE_RERANK_ENABLED` (`:163`) | empty by design (global default) | unaffected |
| `_TYPE_BM25_WEIGHT` (`:121`) | shipped per-type map | unaffected |

So two per-type features — per-type distance thresholds and grouping-disable —
never fire at all on the LLM-analyzer path, because their only keys are exactly
the two classes the prompt forgot. The tuned 0.4 / 0.7 thresholds have never
taken effect there.

A typed option set is a schema, not prose inside a prompt, so this failure
mode cannot recur on the decision path.

**CORRECTED v0.8 — the defect is latent, not live (D-DRAFT-15).**
`LLMQueryAnalyzer` is never constructed in any live path:
`core/src/query/pipeline.py:271` is `self._analyzer = analyzer or
MockQueryAnalyzer()`, and `core/src/store_service/service.py:1199` is
`make_query_analyzer(None)` with a comment recording that wiring an
`LLMProvider` for it is a deliberate follow-up. Classification in the serving
path is **100% rule-based today**, `MockQueryAnalyzer` emits both classes, and
the tables above therefore fire normally — **nothing is currently inert, and
the tuned 0.4 / 0.7 thresholds do take effect.** The defect is a **landmine**
for whoever enables the LLM analyzer, not a present fault. It stays in phase 1
as cheap hygiene but is **not a gate**, and NORA's A4 / A8 / A11 baselines are
sound (an earlier claim that they ran with the defect live is withdrawn).

**Re-escalated at v0.9.** The three-valued knob (§3) is precisely the
mechanism that makes `LLMQueryAnalyzer` reachable, so **the fix becomes a
genuine prerequisite again** — not because the baseline needs it, but because
the `llm` value would otherwise be wrong by construction: silently unable to
emit two of ten classes, with `_TYPE_MAX_DISTANCE` and
`_TYPE_DISABLE_GROUPING` inert whenever that mode is selected. Ship the fix in
the same change that introduces the knob.

**Consequence for §6, revised at v0.9:** with the knob, the `llm` arm is real
rather than synthetic. Arms are rules (live default), llm, decision model, and
fine-tuned decision model.

**Consequence for the premise:** this seam has **no LLM call to displace** — a
decision model here replaces *rules*. D-DRAFT-2's reasons for choosing it
first still hold (query-only exposure, existing baseline, one factory
function, lowest risk), but it will not reduce LLM usage or cost. The decision
points that do spend LLM calls — reranking, SIRA chunk selection, the Stage-2
judge — all read chunk bodies. If token cost rather than capability is the
driver, target ordering deserves revisiting with the architect.

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

Configuration is a **single three-valued knob**, not a boolean flag plus a
roster — revised at v0.9 on the architect side's design, which is
cleaner and maps exactly onto implementations that already exist:

| Value | Implementation | Status today |
|---|---|---|
| `rules` | `MockQueryAnalyzer` | exists; **live default** |
| `llm` | `LLMQueryAnalyzer` | exists, fully written, **never wired** |
| `decision` | `HybridQueryAnalyzer` + `DecisionProvider` | new (§2, §3) |

Named `NORA_QUERY_ANALYZER` to match the established convention
(`NORA_LLM_PROVIDER`, `NORA_EMBEDDING_PROVIDER`, `NORA_STORE_INDEX`,
`NORA_STORE_ENGINE`) and resolved CLI > env var > config > default like the
other `resolve_*` knobs. Default stays `rules`, so an administrator who sets
nothing sees today's behaviour. A **provider roster** still sits *beneath* the
`decision` value, selecting which decision model serves it — the roster did
not disappear, it moved one level down.

**The `llm` value is in scope, and cheaply so.** It was initially treated as
out of scope; that was wrong on three counts. It resurrects code that is
already written and currently does nothing. It restores the measurement arm
§6 had written off as synthetic — with the knob, `llm` is a selectable mode
rather than a hypothetical. And it finally permits the comparison that
D-DRAFT-17 shows **has never been run at this seam**: rules vs LLM vs decision
model, same input, same harness. Cost is a few lines in the factory for the
`nora` lane, plus a thin adapter in the `sira` lane, where
`core/src/store_service/service.py:1193` records that wrapping the service's httpx
LLM in an `LLMProvider` is an outstanding follow-up. Small, not free.

One implementation detail, read rather than assumed: `make_query_analyzer()`
(`analyzer.py:425`) today takes a single `llm_provider=None` argument and
branches two ways — provider supplied → `LLMQueryAnalyzer`, `None` →
`MockQueryAnalyzer`. Adding the decision path therefore needs a signature
extension (a decision-provider argument, or a config read inside the factory),
not just a new branch. Its docstring records the governing posture from
`multi-mno-sira` D-DRAFT-9 — query analysis follows "the same
configured-LLM-or-mock posture as the rest of NORA" — so the decision path
should mirror it: configured decision provider → hybrid, otherwise fall
through to today's two branches unchanged. That fall-through is what makes the
phase-2 identity gate achievable.

**What the serving path reads (added 2026-10-20, D-DRAFT-27).** D-254 makes
SIRA select-synth the serving path, and that path reads only `intent.mnos` and
`intent.releases` from the analyzer. The decision model owns `query_type` and
`doc_type_scope`, and only the NORA `QueryPipeline` consumes them — a pipeline
D-254 disables in current builds. Enabling the knob on a served stack therefore
adds one model call per query whose result nothing reads. The knob stays unset
on served stacks until the decision model owns a field the serving path reads,
or the NORA pipeline serves. Until then it is an evaluation surface.

## 4. Candidates and hardware

Verified first-hand; earlier secondary listings are not load-bearing.

**AutoTrust AI** (Singapore applied-AI lab, `huggingface.co/autotrust`)
publishes the reference open family, all Apache 2.0. **No licence problem
anywhere in it** — it is parked on VRAM alone, and §4.3 shows that constraint
may dissolve. At full precision none of it fits 16 GB:

| Model | Base | VRAM | Fits 16 GB host | Note |
|---|---|---|---|---|
| JEV-9B | Qwen3.5-9B | 24 GB min, 32-48 comfortable (~18 GB download) | no | vision since 2026-10-03 |
| JEV-27B | Qwen3.8-27B | 80 GB+ (~54 GB) | no | keeps 96% of hosted Jev's accuracy at 16 options, KL 0.017 |
| JEV-27B-VL | Qwen3.8-27B + vision | 80 GB+; Q8_0 ~48 GB (99.0% agreement); Q4_K_M ~24 GB (92.9%) | no | 256k inputs; #1 on HF trending 2026-10-08 |
| GEV-26B-Decide | Gemma 4 26B-A4B MoE (~4B active) | 80 GB+; NVFP4 24 GB short ctx (17.1 GiB) | no | adaptive thinking |

The small end — what actually fits — is a separate and less documented field:

| Candidate | Licence | `/v1/systemone` | Fits 16 GB host | Disposition |
|---|---|---|---|---|
| **Kev** 0.8B / 4B | Qwen3.5 bases Apache-2.0 | native | **yes** | **iteration 1** |
| Tev1 0.8B (812 MB) | unverified | unverified | yes | roster candidate |
| Nimble, Lev, Laya, Julia-1 | unverified | varies | yes | roster candidates |
| openjev | **conflicting — see §8** | native + `/v1/decisions` | no | **blocked on licence** |
| AutoTrust family (above) | Apache 2.0 | native | no | parked — synthesis backup host only |

### 4.0 Why the decision block is small, detachable, and cheap to train

AutoTrust's published recipe — **"Blocks of Experts"** — keeps the pretrained
backbone **frozen** and adds a small **detachable** decision block. JEV-9B's
System 1 block is **40.2 million trained parameters, about 0.5% of its
backbone**. Three consequences matter to this design:

- It explains the GGUF trap mechanically (§7): a detachable block ships as
  separate files, so a format that packages only the backbone drops it.
- It explains why the decision block costs nothing in generative quality —
  JEV-9B scores 70.7% on HumanEval with the block attached, *identical* to
  base Qwen3.5-9B, because the backbone is untouched.
- It is the strongest argument that the §4.2 fine-tune is affordable: training
  0.5% of a small model on NORA's labels is a modest job, not a research
  programme. Kev's rank-16 LoRA plus pointer head is the same idea.

These models also integrate **System 1 and System 2**: System 1 answers typed
questions in one forward pass with a calibrated distribution; System 2 is
ordinary generation with step-by-step reasoning. GEV's "adaptive thinking"
switches between them — it answers instantly when confident and reasons first
when not, reportedly taking Minesweeper from 21.0 to 86.0 and Connect Four from
54.0 to 99.5. NORA's phase-1 use is purely System 1; adaptive thinking is a
latency risk on the interactive path and is **out of scope** until measured.

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

Serving, corrected at verification: **Kev does not use vLLM.** It ships its
own server exposing `/v1/systemone` directly — `python -m kev.serve --run
jaredpalmer/kev-4b --port PORT`. vLLM support is in flight (a `vllm.cpp` PR)
but has not landed. This is simpler than planned, not harder: one
self-contained Python server for iteration 1, no vLLM plumbing. The legacy LLM
path stays on Ollama, so the arms still do not contend, and the GGUF ban
(D-DRAFT-3) still binds — it rules out the Ollama/llama.cpp distribution path
regardless of which server the decision model uses. vLLM remains the serving
route for the larger candidates (§4.3, AutoTrust family) if they are ever
stood up.

**bf16 is mandatory on the 16 GB GPU host, not optional.** Kev's card states "4B fp32
needs ~16 GB", which exactly saturates the card leaving nothing for context.
`KEV_DTYPE=bf16` roughly halves it to ~8 GB. A flag that must be set.

### 4.3 AWQ preserves the decision head — JEV-9B may reach the 16 GB GPU host

The VRAM parking above assumed full precision and NVFP4 only. A third path
exists and inverts the GGUF trap.

Precedent: **`aioutfitters/JEV-27B-VL-AWQ`**, a 4-bit AWQ build produced with
`llm-compressor` and served on vLLM, in which **the decision head (`lm_head`)
and the vision tower are deliberately kept in bf16 while every other text
layer is quantized** — W4A16_ASYM, group size 128, asymmetric int8 zero
points, pack-quantized / compressed-tensors. Where GGUF *drops* the decision
block, this approach *protects* it and compresses only the backbone.

Applied to **JEV-9B** (18 GB bf16): 4-bit backbone weights land near 5 GB, the
bf16 head is small, and KV cache for single short queries is modest — so it
should fit the 16 GB GPU host with headroom. Its published figures are the strongest
of any candidate here: mean KL ≈ 0.019 against hosted Jev, **90% of Jev's
accuracy at 16 options**, 2.6× faster than JEV-27B. Sixteen options is exactly
`_FEATURE_KEYWORDS`' cardinality (§2), so that datapoint is directly on-shape
for NORA.

Two honest caveats, both measurable rather than arguable:

- **No JEV-9B-AWQ was found published.** NORA would produce it with
  `llm-compressor`. A documented path, but real work, and it must be verified
  against the §7 file-list check afterwards.
- **Quantization costs accuracy on top of the 90%.** JEV-27B-VL's Q4_K_M held
  92.9% agreement with full precision and Q8_0 held 99.0%; AWQ W4A16
  typically beats Q4_K_M, but the figure for this model is unknown until
  measured on NORA's labelled set.

**Consequence for the roster:** the candidate field is two-horse, not
one-horse. **Kev** — tiny, native `/v1/systemone`, LoRA-trainable, unverified
provenance and adapter licence. **JEV-9B-AWQ** — reference family, clean
Apache 2.0, published benchmarks, needs a quantization step NORA performs
itself. Both fit the 16 GB GPU host and both belong in the phase-3 roster; Kev remains
first to stand up because it needs no quantization work.

### 4.1 "Head-bearing" is the speed mechanism, not a drawback

Stated explicitly because it is easy to invert: **every candidate in this
design is a head-bearing model, including Kev, and that is precisely why they
are fast.** Hosted Jev is one too.

A generative LLM is slow because it is autoregressive — producing N tokens
costs N serial forward passes, each depending on the last, so latency scales
with output length. A decision head removes the loop entirely: one forward
pass, then a thin trained layer reads the final hidden state and emits a
probability distribution over the K options directly. **Zero tokens
generated, and latency is independent of K.** Kev's bilinear pointer head
scores each option's hidden state against the question's `<decide>` hidden
state and softmaxes; openjev describes the same property as "one read
generates label token probabilities directly from model outputs".

Base size still sets the cost of that single pass, which is why the small end
of the field is attractive rather than merely affordable: Kev-0.8B on a local
the 16 GB GPU host should beat hosted Jev's quoted 70-500 ms end-to-end, because it is both
a far smaller base and has no network hop. The category's speed claim is not
something NORA gives up by self-hosting small — it is something self-hosting
small improves.

### 4.2 Prior-art warning — about accuracy, not speed

NORA already ships a head-bearing model: `CrossEncoderReranker`
(`reranker.py:90`, `ms-marco-MiniLM-L6-v2`), the same architecture class with
a scoring head instead of a decision head. The **A11** run — one of NORA's
own labelled eval runs in `STATUS.md`, not an external benchmark — had it
net-zero and slightly regressive at 86.4% overall / 62.3% accuracy, diagnosed
as out-of-distribution on telecom spec text.

**CORRECTED v1.0 — the gap is the label set, not the domain.** The framing
below (and in D-DRAFT-10) read as "generic weights are weak on telecom text",
which is wrong for this target and would mislead a reader. Our classifier reads
plain English questions and **never touches spec prose**, so telecom
vocabulary is irrelevant to it. What Kev has never seen is **NORA's label
set** — it was trained to sort banking intents, news topics and film
sentiment, not to place a question among ten NORA `QueryType` values, and
`RELEASE_DIFF` / `TRACEABILITY` are our taxonomy rather than English concepts.
Some boundaries are not derivable from English at all but are NORA
conventions: `analyzer.py` documents that "explain the X authentication
requirements" routes to `SUMMARIZE` rather than `SINGLE_DOC` *even though it
names a plan*, because the user wants the topic surveyed. No language
understanding yields that.

Three consequences follow, all of which soften the earlier position:

- **Kev classifies zero-shot.** `/v1/systemone` takes the option set at
  inference time and the pointer head scores each option against the question,
  so no training is a prerequisite to running it. Iteration 1 measures
  zero-shot first.
- **The remedy, if needed, is an ordinary task SFT** on a few hundred labelled
  questions teaching category boundaries — drawn from the set phase 1 builds
  for evaluation anyway. One artefact, two uses. It is reached for only if the
  confusion matrix shows errors clustering on boundaries, not by default.
- **The A11 parallel is weaker than stated.** That cross-encoder scored
  query-against-chunk pairs over spec prose and went OOD on the domain; this
  classifier sees no prose. A11 remains a caution about generic models on NORA
  tasks, but Kev's own 0.790 is the number to expect, not A11's.

Domain-adaptive pre-training stays rejected (D-DRAFT-12) and is unaffected by
any of this.

**Kev-4B's own model card confirms the OOD figures independently**, which moves
them from inference to documented fact. Published figures: in-distribution accuracy
0.854, **out-of-domain accuracy 0.790**, out-of-domain Brier 0.328, and **ECE
0.096, explicitly described as uncalibrated out of domain**; locked test 0.856
ID / 0.806 OOD. Its training suite is ~13,576 generic text-classification
records over two epochs — banking77, BoolQ, AG News, MultiNLI, SST5, Yelp,
TREC, DBpedia, Amazon Reviews, IMDB — containing nothing resembling telecom
requirement specifications. The card's limitations state that
"product-shaped questions with no training analogue are not guaranteed",
which is exactly what NORA's `QueryType` classification is.

Two consequences. Out-of-the-box **calibration — the category's headline
selling point — is not available** for this corpus at ECE 0.096. And the LoRA
fine-tune stops being an enhancement and becomes **the experiment itself**.
Its scale is encouraging: 13.5k records over two epochs is a small job, which
argues for resolving §8's fine-tuning-budget question in favour of keeping it
in this strand.

The lesson is strictly about **accuracy of generic weights on domain text**,
and carries no implication about the architecture or its latency. A generic
Kev should be expected to hit the same OOD wall for the same reason, which is
the strongest argument for budgeting the LoRA fine-tune rather than treating
it as optional. NORA has already paid for this lesson once.

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

Four arms, each one value of `NORA_QUERY_ANALYZER` (§3) or a roster entry
under it:

1. **`rules`** — `MockQueryAnalyzer`. The live baseline, and the thing a
   decision model must actually beat.
2. **`llm`** — `LLMQueryAnalyzer`, defect fixed. Never measured at this seam
   before (D-DRAFT-17), so this arm answers a standing open question
   independent of decision models.
3. **`decision`, generic Kev-4B** — expected weak per §4.2 (0.790 OOD, ECE
   0.096 on its own card).
4. **`decision`, fine-tuned Kev-4B** — conditional on §8's fine-tuning-budget
   question; §4.2 argues it is the arm that actually matters.

**Weight the confusion matrix by downstream divergence.** Because §1a's knob
fan-out means every class selects a different retrieval configuration, not all
confusions cost the same. `FACT` ↔ `SUMMARIZE` is catastrophic — tight top_k
+ strict 0.4 threshold + rerank on, versus wide top_k + 0.7 threshold +
grouping off: opposite configurations. `CROSS_DOC` ↔ `FEATURE_LEVEL` is mild
— both rewrite-enabled, both top_k 25. A classifier at 95% that errs on the
first pair can be worse end-to-end than one at 85% that errs on the second, so
the reported metric must reflect damage rather than label mismatch.

**Two levels of evaluation, which can disagree.** *Intrinsic* — the weighted
confusion matrix on the labelled set. *Extrinsic* — end-to-end pipeline
quality on the 18-Q / golden sets. Both are required; a gain in one does not
imply a gain in the other.

**Why an eval harness is load-bearing rather than overhead.** Rules are
deterministic and inspectable: a misfire is read and fixed at the rule. A
model offers no such visibility — it can only be measured. Introducing a model
is therefore what *creates* the need for this harness; it is the replacement
for the debuggability being traded away, and that trade should be stated to the
architect explicitly.

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

- **Legacy is the default** at every phase. An administrator who sets no flag sees
  today's behaviour, byte-identical.
- **No external egress.** Every candidate is self-hosted in-corp
  (D-DRAFT-1). No decision-model traffic leaves the network.
- **No proprietary corpus content** in logs, reports, or test fixtures — the
  standing project rule. Note that the phase-1 target sees only query text,
  not corpus content; the other six LLM decision points do not share that
  property and each needs its own assessment before being touched.
- **GGUF is banned** for head-bearing decision models (D-DRAFT-3). vLLM only.
- **Decision heads must be asserted loaded** before any accuracy number is
  recorded. Concrete check, per AutoTrust's own guidance: the model's file
  list must contain `head.safetensors`, an adapter folder, and
  `calibration.json`. **A hollow distribution contains only `.gguf` files.**
  The missing components in a stripped build are the decision adapter, the
  24-slot decision head, and the calibration temperatures. Phase 3 asserts
  this in code before recording a single number, not by inspection.
- `QueryIntent` is the contract and does not change.

## 8. Open points for adjudication

1. **Module placement** — `core/src/decision/` as a 17th package (proposed)
   versus extending `core/src/llm/`.
2. **openjev's licence is in conflict and the project is blocked until it is
   resolved.** Its GitHub repository states Apache-2.0, while a secondary
   survey records openjev (27B) as **CC BY-NC 4.0 — commercial use
   prohibited**. The two sources also disagree on the model itself
   (DiffusionGemma 26B-A4B on vLLM/MLX, versus a 27B served through
   llama.cpp). For a corporate deployment an NC licence is a hard blocker, so
   openjev stays out of the roster until legal confirms the licence from the
   repository itself. Not resolved here on purpose — this design does not get
   to pick which source is right.

   **Do not assume "internal use only" satisfies NC.** CC BY-NC 4.0 turns on
   whether the use is "primarily intended for or directed towards commercial
   advantage" — a purpose test, not a distribution test. A for-profit company
   running a model internally to support its commercial business is generally
   commercial use; internal deployment does not exempt it. Creative Commons
   additionally does not recommend CC licences for software and acknowledges
   NC is ambiguous, and the status of model weights and fine-tuned derivatives
   under copyright is unsettled. **Practical routing: this costs nothing to
   avoid.** openjev is already parked on VRAM (it does not fit the 16 GB GPU host), so
   the recommendation is to drop it rather than spend legal time — revisit
   only if the synthesis backup host frees up and it is actually wanted.

   **Related, and in scope for the same intake review:** Kev's *bases*
   (Qwen3.5) are Apache-2.0, but the licence covering Kev's own LoRA adapter
   and pointer head is a separate artifact and is **not yet verified**. Base
   licence does not imply adapter licence.
3. ~~**Canonical Kev upstream.**~~ **CLOSED 2026-10-13.** Canonical upstream
   is `jaredpalmer/kev` — the original, no fork marker, 9.1k stars, 343
   commits; the other `kev` repositories are forks of it. **Apache-2.0 covers
   the repository, the LoRA adapter and the pointer head**, and the Qwen3 /
   Qwen3.5 / Qwen3.8 bases are Apache-2.0 too. Weights at
   `huggingface.co/collections/jaredpalmer/kev`. Remaining trivial item for
   pull time: the `kev-4b-qwen3` card cites `Qwen/Qwen3-4B-Base` while the
   repo overview says Qwen3.5-4B-Base, so confirm which variant is being
   pulled.
4. **Fine-tuning budget.** §4's prior-art warning argues the generic-Kev arm
   is likely to underperform for the same OOD reason the cross-encoder did.
   Is the LoRA fine-tune in scope for this strand, or a successor?
5. **Architect questions still open:** production image/stack name for the
   phase-5 rebuild; exact specification of the synthesis backup host.
6. **Phase-5 criteria.** What measurement result justifies flipping the
   default? Stating it now prevents the decision being relitigated later
   against a moving bar.
