### D-DRAFT-1 — Hosted Jev rejected; target is an open-weights Jev-class model served in-corp

**Date:** 2026-10-13
**Context:** Architect directive asks for a "proper Jev flow" replacing NORA's
current LLM-based decision points. TypeSafe AI's Jev is the reference product
for the "System One" decision-model category (typed bool / score / choice
outputs with calibrated probabilities, single forward pass, 70-500 ms,
$0.042/M input tokens, output free).
**Decision:** The hosted Jev API is disqualified. Pursue the *category* via an
open-weights Jev-class model served inside the corporate network.
**Why:** Jev is closed-weights, hosted-only — no VPC, no on-premise, no
self-hosting option published. Service runs from the US West Coast with no
region choice; retention is "as long as reasonably necessary" (zero-retention
negotiable only via enterprise sales); SOC 2 Type II is third-party reported
but unconfirmed in TypeSafe's own documents; no ISO 27001, no BAA. NORA's
corpus is proprietary MNO requirement content, `CLAUDE.md` bars that content
from logs and reports, and the build/serve environment permits **no external
API calls at all** (confirmed by the architect-side operator this session).
There is no configuration in which hosted Jev runs against this corpus.
**Consequences:** No TypeSafe contract or budget line is needed. Every
candidate must be self-hostable on internal hardware. "Jev" in this strand's
later artifacts means Jev-*class*, not the product — worth stating explicitly
to the architect so nobody plans around vendor access.

### D-DRAFT-2 — First target is query classification, not reranking

**Date:** 2026-10-13
**Context:** Seven LLM-backed decision points exist in NORA (query intent
classification, rerank scoring, query rewriting, taxonomy feature mapping,
SIRA chunk selection, query expansion, golden-eval Stage-2 judge). The
experiment needs one.
**Decision:** `LLMQueryAnalyzer` (`core/src/query/analyzer.py:353`) is the
first and only target for the initial phase.
**Why:** It emits a `choice` — the decision-model category's native shape. It
sees only the user's query, never corpus text, so it is testable under the
narrowest possible data permission. `make_query_analyzer()` is already a
factory, so swapping an implementation touches one function. Decisively, it
**already has a rule-based twin** (`MockQueryAnalyzer`, same `analyze()`
interface), which gives a three-way comparison — rules vs LLM vs decision
model — on an existing interface with no new abstraction.
Rejected: **reranking** (`OpenAIRerankChat`, `reranker.py:519`), despite being
mechanically the most decision-model-shaped code in the repo (it already asks
an LLM for an integer score per doc, batched). It reads chunk bodies, and it
sits inside the `nora-retrieval-quality` problem — cold for ten weeks, with
both NORA lanes excluded from team-eval — so any effect would be
unattributable against existing retrieval starvation.
Rejected for now: **taxonomy feature mapping**, the bigger prize (STATUS flag
2026-05-03 records a 20pp eval swing traced to its non-determinism), because
it is a build-time stage with graph-topology consequences — too heavy to carry
a first comparison.
**Consequences:** The other six decision points are explicitly out of scope
until query classification reports. Each needs its own egress assessment,
since four of them read corpus content.

### D-DRAFT-3 — GGUF builds are disqualified for Jev-class models; vLLM is the serving path

**Date:** 2026-10-13
**Context:** NORA serves local models through Ollama, which is GGUF-based. The
obvious integration path is to pull a decision model and point the analyzer at
it.
**Decision:** GGUF distributions of Jev-class models must not be used. Serving
is via vLLM (GEV-26B-Decide ships a pre-configured vLLM serve script).
**Why:** Published GGUF builds of these models **contain no decision
component at all** — the heads that produce typed calibrated outputs live in
separate small files that GGUF conversion drops. The result is a plain Gemma
that generates tokens, wearing a decision-model name. It loads, it answers,
and it is silently the wrong thing. This independently confirms the
architect's suspicion that the current runtime "does not seem to support a Jev
decision model."
**Consequences:** Adds a serving runtime (vLLM) that NORA does not operate
today — an infrastructure commitment, not a config change, and a real cost
line against this strand. Any candidate evaluation must assert the decision
heads are actually loaded before a single accuracy number is recorded; a
silent degradation to base-model token generation would invalidate an entire
comparison.

### D-DRAFT-4 — GEV-26B-Decide is DGX-Spark-only; the A4000 cannot host it

**Date:** 2026-10-13
**Context:** GEV-26B-Decide (AutoTrust AI, Apache 2.0, ~26B MoE, multimodal,
calibrated probability per option, adaptive thinking, ~45 ms System-1 forward
pass) is the strongest open Jev-class candidate surfaced so far. Available
internal hardware named by the architect: a DGX Spark and an A4000 with 16 GB
VRAM.
**Decision:** If GEV-26B is selected, DGX Spark is its only viable host.
**Why:** bf16 needs 80 GB+ (51.1 GiB resident). The NVFP4 build cuts memory
66% to 17.1 GiB and still requires 24 GB+ — it exceeds the A4000's 16 GB on
weights alone, before any context. DGX Spark's unified memory clears the
requirement and its GB10 is Blackwell, so NVFP4 is architecturally native.
**Consequences:** Selecting GEV-26B ties the decision path to one machine,
which has availability, contention, and single-point-of-failure implications
for an interactive query-time dependency. Smaller open candidates (0.6B-9B
Qwen3.5-based; 322-421M ModernBERT-class) are what actually fit the A4000,
with an open accuracy question. Requires confirming vLLM support on DGX Spark.

**Amended 2026-10-13:** hardware availability clarified by the architect. DGX
Spark is currently committed as the backup host for LLM synthesis, so it is
contended and likely unavailable near-term — explicitly *not* ruled out, but
not plannable. The A4000 (16 GB) is the practical target, and can also serve as
a backup. Consequence: GEV-26B-Decide is **out of scope for the first
iteration** on hardware grounds (17.1 GiB NVFP4 weights exceed 16 GB before
context), and the first-iteration shortlist is the small end of the field —
ModernBERT-class (322-421M), sub-9B Qwen3.5-based builds, and SemIf-style
constrained decoding over a model already resident. GEV-26B re-enters if and
when DGX Spark frees up.

### D-DRAFT-5 — Baselines are measured for accountability; they do not gate the exploration

**Date:** 2026-10-13
**Context:** Published counter-evidence on the decision-model category argues
small purpose-trained classifiers often match or beat decision models on
trained tasks, and that the calibration advantage is overstated (see Why
below). An earlier draft of this decision treated that as grounds for the
exploration to terminate at "use the classifier instead."
**Decision:** The rule-based (`MockQueryAnalyzer`) and small-classifier arms
are measured as **reference baselines**, reported honestly, and do **not**
gate the Jev-class exploration. The strand proceeds with the decision-model
integration regardless of whether a baseline wins on accuracy.
**Why:** The strand's objective is capability, not a leaderboard win (see
D-DRAFT-6) — a baseline that beats the decision model is a useful result to
*record*, not a reason to stop. Baselines are still mandatory: without them
the experiment produces an unanchored number and cannot tell anyone whether
the decision path helped, which is the difference between exploration and
decoration. The counter-evidence that motivates them: a 200M DeBERTa scored
89.01% at 54 ms against hosted Jev's 86.35% at 348 ms on prompt-injection
detection; a 125M model hit 80.27% at 33 ms on content safety; hosted Jev runs
overconfident at 0.81 mean confidence against 0.74 actual accuracy (ECE
0.074); and JEV-9B's published ECE of 0.0007 is computed against the teacher's
distributions — closeness to Jev, not accuracy against truth (0.0759
independently).
**Consequences:** Phase 1 carries a labelling step the directive does not yet
budget. Success criteria must be stated as capability + accountable
measurement, not "beat the LLM," or a null accuracy result will read as
failure when it is not. Reporting must separate three questions that will be
conflated otherwise: does the decision path *work*, is it *faster*, and is it
*more accurate* — the answers can legitimately be yes / yes / no.

### D-DRAFT-6 — The strand's objective is capability-building under configurability, not a first-shot win

**Date:** 2026-10-13
**Context:** Decision models ("System One" models) are an emerging category
with a real speed advantage and growing adoption. The architect-side decision
is to build in-house expertise with them now rather than wait for the category
to settle, using NORA's query classification as the vehicle.
**Decision:** This strand ships a configurable Jev-class decision path in
NORA and explores its effectiveness. The deliverable is working, configurable
integration plus recorded expertise — not a proven accuracy improvement. The
work proceeds irrespective of baseline comparison outcomes.
**Why:** Deliberate capability investment. The category is fast and gaining
adoption; the expertise transfers to NORA's six other LLM-backed decision
points and to classification work beyond NORA. Waiting for external consensus
forfeits the learning window, and the integration cost is bounded because the
seam is one factory function (`make_query_analyzer`). An accuracy win is a
possible bonus, not the success condition.
**Consequences:** Legacy remains the configurable default throughout, so the
exploration never risks production behaviour. Success criteria are: the
decision path runs in a configurable manner, candidate swapping works, and
measured per-class accuracy / determinism / latency are recorded against
baselines. "The baseline won" is a successful outcome of this strand, not a
failed one. The strand should also bank negative findings (the GGUF trap in
D-DRAFT-3 is already one) as first-class deliverables.

### D-DRAFT-7 — The decision model owns only the choice- and bool-shaped fields of QueryIntent; extraction stays with the existing paths

**Date:** 2026-10-13
**Context:** `LLMQueryAnalyzer.analyze()` returns a whole `QueryIntent`
(`core/src/query/schema.py:62`) with nine populated fields, not a single
label. A decision model emits only `bool` / `score` / `choice`, so it cannot
be a drop-in replacement for the method.
**Decision:** Partition `QueryIntent` by output shape. The decision model owns
`query_type` (a 10-way `choice`) and `doc_type_scope` (a 3-way `choice`), and
optionally `mnos`, `likely_features` and `plan_ids` as **parallel booleans,
one question per closed-vocabulary candidate**. The open-extraction fields —
`entities`, `concepts`, `releases`, `standards_refs` — stay on the existing
regex paths in `MockQueryAnalyzer`.
**Why:** The partition follows the data, not preference. `query_type` and
`doc_type_scope` are closed option sets — native `choice` shape. `mnos`
(VZW/TMO/ATT), `likely_features` (keys of `_FEATURE_KEYWORDS`) and `plan_ids`
(five values) are closed-vocabulary multi-selects, which decompose exactly
into the per-candidate parallel questions the category is built for — one
`state` (the query) evaluated against many questions in one request, well
inside a 64k/32k budget for a single query. `entities`, `concepts`,
`releases` and `standards_refs` are free-text span extraction with no option
set, so no decision model can produce them at all. `MockQueryAnalyzer` already
implements every one of these extractors in regex
(`_extract_entities`, `_extract_concepts`, `_extract_releases`,
`_extract_standards`), so the non-decision half of the work is already
written and tested.
**Consequences:** The integration is a **hybrid** analyzer, not a swap: a
decision-model classifier composed with the existing rule-based extractors.
This is architecturally cleaner than today's `LLMQueryAnalyzer`, which asks one
LLM call to do classification and extraction together and falls back wholesale
to `MockQueryAnalyzer` on any JSON parse failure. It also fixes a live defect
by construction: today's LLM prompt enumerates only 8 of the 10 `QueryType`
values, omitting `SUMMARIZE` and `FACT`, so the LLM analyzer can never emit
them and their per-type knobs (wide top_k + grouping off; tight top_k + strict
threshold + rerank on) are silently dead. A typed option set is a schema, not
prose in a prompt, so that failure mode becomes impossible.

### D-DRAFT-8 — The decision model runs before query expansion; the existing per-type gate forces it

**Date:** 2026-10-13
**Context:** Directive phase 3 asks explicitly whether the Jev-class decision
step should run before or after query expansion.
**Decision:** Before. Classification stays the first stage of the query
pipeline; expansion remains downstream of it.
**Why:** Not a preference — the code already forecloses the alternative.
`core/src/query/pipeline.py:500` gates expansion on the classification result:
`if _TYPE_REWRITE_ENABLED.get(intent.query_type, False):`. `analyze()` runs at
`:420` and `rewrite()` at `:501`, so expansion is a *consequence* of
classification, not a peer stage. Placing the decision model after expansion
would leave nothing to decide whether to expand. The same dependency holds for
every other per-type knob — `top_k`, BM25 weight, rerank enable, distance
threshold, grouping disable, and the synthesis system prompt all read
`intent.query_type`.
**Consequences:** The decision model sits at the earliest point of the
interactive path, so its latency is in series with the entire downstream
pipeline — this is what makes the category's single-forward-pass claim
(~45 ms) materially valuable, and it argues for the small candidates over
GEV-26B even once DGX Spark frees up. More importantly, a misclassification
propagates into *every* downstream knob, so **per-class accuracy, not overall
accuracy, is the measurement that matters**: a full confusion matrix across
all 10 `QueryType` values is required output of the comparison, not a
nice-to-have. A candidate that is 90% accurate overall but systematically
confuses `FACT` with `SUMMARIZE` would invert two opposed retrieval
configurations (tight top_k + strict threshold + rerank on, versus wide top_k
+ grouping off) and could be worse in practice than the rule-based baseline.

### D-DRAFT-9 — `/v1/systemone` is NORA's internal decision-provider interface

**Date:** 2026-10-13
**Context:** Directive phase 3 requires a selectable roster of decision
models, mirroring the existing LLM roster. Several open Jev-class
implementations expose TypeSafe's own `/v1/systemone` HTTP API (Kev, Rizzo
Flow, `razorback16/openjev`).
**Decision:** Define a `DecisionProvider` protocol speaking `/v1/systemone`
(typed `bool` / `score` / `choice` questions against one shared `state`),
with concrete providers behind it and a roster selecting among them. Legacy
remains the configurable default.
**Why:** Mirrors a pattern this codebase has already validated. NORA's
`OpenAICompatibleProvider` speaks `/v1/chat/completions` over stdlib urllib
with no SDK dependency, which is what made the LLM roster vendor-neutral and
let `llm-model-discovery` add model selection without touching providers.
Adopting the de-facto decision-model wire format buys the same property:
swapping candidates becomes a config change, and if hosted Jev ever becomes
reachable (it is not today — D-DRAFT-1) it is the same interface with no
rework. Protocol-based abstraction is also the stated project convention
(`LLMProvider`, `EmbeddingProvider`, `VectorStoreProvider`).
**Consequences:** Candidates that do not expose `/v1/systemone` —
ModernBERT-class (Laya, Von) and SemIf-style constrained decoding over a
resident model — need a thin in-process adapter implementing the protocol
rather than an HTTP hop. That is acceptable and keeps the roster honest about
what each arm actually is. Module placement still to decide (a new
`core/src/decision/` package versus extending `core/src/llm/`); either way it
needs its own `MODULE.md` per project convention, shipped in this branch per
the CLAUDE.md branch-flow rule.

### D-DRAFT-10 — Kev leads iteration 1; openjev and GEV-26B are parked on VRAM

**Date:** 2026-10-13
**Context:** With vLLM confirmed in production and the A4000 (16 GB) as the
practical host, the iteration-1 lead candidate had to be a native
`/v1/systemone` implementation that fits 16 GB. Shortlist verified first-hand
rather than from the earlier secondary listing.
**Decision:** Kev (0.5B-9B family) leads iteration 1, at 0.8B or 4B. openjev
and GEV-26B-Decide are parked on hardware grounds and re-enter if DGX Spark
frees up.
**Why:** Verified properties. Kev checkpoints are a rank-16 LoRA adapter plus
a small bilinear pointer head on a Qwen3.5 base — Kev-0.8B on
Qwen3.5-0.8B-Base and Kev-4B on Qwen3.5-4B-Base, both Apache-2.0. The readout
is a trained bilinear pointer head scoring each option's hidden state against
the question's `<decide>` hidden state, softmaxed to probabilities — not a
letter-row argmax. The API matches TypeSafe's System One, so it validates the
D-DRAFT-9 protocol choice natively instead of through an adapter, and the
TypeSafe Python SDK can be pointed at a local server. Ecosystem support is
landing (a vllm.cpp PR adding Kev; a system1-omni issue tracking the family).
Decisively, **Kev is trainable** — which collapses the D-DRAFT-5 tension: if
the counter-evidence is right that a small purpose-trained classifier beats a
generic decision model, then Kev fine-tuned on NORA's labelled queries *is*
that classifier, with a decision-model interface and calibrated outputs. The
two arms merge in iteration 2 rather than competing.
Parked: **openjev** (Apache-2.0, DiffusionGemma 26B-A4B, native
`/v1/systemone` plus `/v1/decisions`, 698 stars, 30 ms p50 on an RTX PRO 6000,
confidence defined as `1 - H(p)/ln K`) requires 24 GB+ VRAM on its vLLM
backend — it does not fit the A4000. Its MLX backend needs ~16 GB on Apple
silicon, which NORA does not deploy on. Worth revisiting on DGX Spark; its
heads are integrated into the weights rather than shipped separately, which is
a point in its favour against the D-DRAFT-3 trap.
**Consequences:** Iteration 1 pulls external model code and weights into the
corporate network, so **the canonical Kev upstream must be established before
anything is pulled** — searches surface many near-identical forks
(`jaredpalmer`, `chi2liu`, `song007`, `Radexito` and others) with no obvious
provenance ordering. This is a supply-chain review item, not a formality, and
it belongs to whoever owns software intake. Secondary consequence: no
published accuracy or calibration benchmarks were found for either Kev or
openjev, so NORA's own labelled set is the only evidence that will exist for
this choice — which raises the priority of building it.

### D-DRAFT-11 — This strand owns the `SUMMARIZE`/`FACT` prompt-omission fix, as phase-1 work

**Date:** 2026-10-13
**Context:** `QueryType` carries ten members. `LLMQueryAnalyzer`'s prompt
(`analyzer.py:378`) enumerates eight and omits `summarize` and `fact`, so
neither class can ever be emitted on the LLM-analyzer path.
`MockQueryAnalyzer` emits both (`:269`, `:283`). Verified blast radius against
the per-type maps in `pipeline.py`: `_TYPE_MAX_DISTANCE` (`:104`) has exactly
two keys, `FACT: 0.4` and `SUMMARIZE: 0.7`; `_TYPE_DISABLE_GROUPING` (`:199`)
has exactly one, `SUMMARIZE`. Both features are therefore **entirely inert**
whenever the LLM analyzer is active. `_TYPE_REWRITE_ENABLED` (`:151`) loses
only its `SUMMARIZE` entry; its other six types are unaffected.
**Decision:** This strand fixes the defect as part of phase 1, rather than
routing it to a separate bugfix branch or asking the architect to adjudicate
ownership.
**Why:** It sits inside this strand's target seam, it was found by this
strand's characterization work, and it blocks this strand's phase-1 gate —
baselines are meaningless if the legacy arm is measured against code where two
per-type features silently never fire. Routing it elsewhere would stall phase
1 behind another queue for a fix that is a prompt-string edit plus a
regression test.
**Consequences:** The legacy arm must be reported as "legacy, defect fixed"
and is **not** comparable to NORA's historical eval baselines (A4 88.0% /
67.6%, A8 88.3% / 64.3%, A11 86.4% / 62.3%). Needs checking which analyzer
those runs actually used — if they ran with a configured LLM provider they
went through `LLMQueryAnalyzer` and the per-type distance thresholds and
grouping-disable were inert throughout, which would mean those tuned 0.4 / 0.7
values have never taken effect in any recorded baseline. If so, the fix is a
behaviour change to the legacy path in its own right and needs its own
before/after measurement, independent of anything to do with decision models.

### D-DRAFT-12 — Iteration 1 runs Kev-4B on the A4000 as-is; no quantization work in scope

**Date:** 2026-10-13
**Context:** Two viable candidates reach the A4000: Kev-4B directly, and
JEV-9B after a self-produced 4-bit AWQ conversion NORA would be first to
attempt (§4.3 of the design). The strand's objective is exploration and
expertise, not deployment optimization (D-DRAFT-6).
**Decision:** Iteration 1 uses **Kev-4B on the A4000, unmodified**. No
quantization work, no AWQ conversion, no DGX Spark dependency.
**Why:** It works out of the box on hardware already available with a runtime
already in production, so the first iteration spends its effort on the seam,
the labelled set and the measurement rather than on model engineering. The
JEV-9B-AWQ route is better-pedigreed (clean Apache 2.0, published figures —
mean KL ~0.019 to hosted Jev, 90% of its accuracy at 16 options) but unproven:
no JEV-9B-AWQ is published, so NORA would be first, and the attempt could cost
a day or a week. Carrying unproven conversion work inside a first exploration
risks the strand failing for reasons unrelated to decision models.
**Consequences:** The accuracy ceiling of iteration 1 is whatever a 4B generic
decision model achieves on telecom text — likely modest, per the §4.2
out-of-distribution warning. That is acceptable and expected: the deliverable
is a working configurable path plus measurement, not a win. JEV-9B-AWQ stays
in the roster as a later entry, and becomes attractive without any conversion
work at all if DGX Spark frees up. The general rule recorded for whoever picks
this up next: **small models fit the A4000 as-is; large models fit DGX Spark
as-is; everything else needs shrinking.** Note that DGX Spark's unified memory
will fit the large models but is slower than dedicated GPU memory, so "fits"
should not be read as "fast" there until measured.

### D-DRAFT-13 — Kev provenance and licence cleared; three as-built corrections

**Date:** 2026-10-13
**Context:** D-DRAFT-10 selected Kev while flagging two unresolved intake
items: which of the many `kev` repositories is canonical, and whether the
adapter/head licence differs from the Apache-2.0 Qwen base. Both are now
resolved first-hand, and the model card corrects three assumptions carried in
the design.
**Decision:** Kev is cleared for use. Canonical upstream is
**`jaredpalmer/kev`** — the original (no fork marker), 9.1k stars, 343
commits. **Apache-2.0 applies to the repository, the LoRA adapter and the
pointer head**, with Qwen3 / Qwen3.5 / Qwen3.8 bases also Apache-2.0. Design
§8 open point 3 (canonical upstream + adapter licence) closes. Open point 2
remains openjev's licence conflict, which is unaffected.
**Why:** Verified against the repository and the `kev-4b-qwen3` model card
rather than secondary sources. Kev 1.0 shipped four sizes — Kev-0.8B, Kev-4B,
Kev-9B (Qwen3.5 bases) and Kev-27B (Qwen3.8, post-trained). Weights are on the
Hugging Face Hub under `huggingface.co/collections/jaredpalmer/kev`.
**Consequences — three corrections to the design as written:**
1. **Kev does not use vLLM.** It ships its own server exposing
   `/v1/systemone` directly: `python -m kev.serve --run jaredpalmer/kev-4b
   --port 8008`. vLLM support is in flight (a `vllm.cpp` PR) but not landed.
   This is simpler than planned, not harder — one self-contained Python
   server. The GGUF ban (D-DRAFT-3) is unaffected and still binds.
2. **bf16 is mandatory on the A4000, not optional.** The card states "4B fp32
   needs ~16 GB", which exactly saturates the card with nothing left for
   context. `KEV_DTYPE=bf16` roughly halves it to ~8 GB. A config flag that
   must be set, not a default to assume.
3. **Generic Kev-4B will be weak and badly calibrated on NORA's corpus, by
   its own documentation.** Published figures: in-distribution accuracy
   0.854, **out-of-domain accuracy 0.790**, out-of-domain Brier 0.328, and
   **ECE 0.096 — explicitly described as uncalibrated out of domain**. Locked
   test 0.856 ID / 0.806 OOD. Latency ~45 ms on an H100, so materially slower
   on an A4000. Its training suite is ~13,576 generic text-classification
   records over two epochs (banking77, BoolQ, AG News, MultiNLI, SST5, Yelp,
   TREC, DBpedia, Amazon Reviews, IMDB) — nothing resembling telecom
   requirement specifications. The card's own limitations state that
   "product-shaped questions with no training analogue are not guaranteed",
   which is exactly what NORA's `QueryType` classification is.
   This independently confirms the §4.2 out-of-distribution warning derived
   from NORA's A11 cross-encoder result, and it retires calibration — the
   category's headline selling point — as a benefit available out of the box.
   **The LoRA fine-tune is therefore not an enhancement; it is the
   experiment.** Scale is encouraging: 13.5k records over two epochs is a
   small job, which strengthens the case for resolving §8's fine-tuning-budget
   question in favour of including it in this strand.

### D-DRAFT-14 — No domain-adaptive pre-training for query classification; SFT on labelled queries only

**Date:** 2026-10-13
**Context:** Kev-4B's own card documents weak, uncalibrated out-of-domain
behaviour (0.790 accuracy, ECE 0.096) on a training suite of generic
text-classification benchmarks. One proposed remedy was CPT — "seasoning" the
base on NORA's telecom requirement specs before task fine-tuning.
**Decision:** CPT is **out of scope** for the query-classification target.
Adaptation is **SFT on labelled queries only**.
**Why:** The classifier's input is **user questions**, not corpus text — "What
are all the data-retry requirements?", "Compare VZW and TMO on IMS
registration". What separates `RELEASE_DIFF` from `TRACEABILITY` is **question
shape**, not telecom vocabulary. CPT teaches the language of *documents*, a
different distribution from the language of *questions about* documents, and
could plausibly degrade the task by pulling the representation toward prose
never seen at inference. Short English questions over ten stable classes is a
favourable shape for SFT, so the simpler intervention is also the better-aimed
one. Two further costs made the rejection easy rather than close: Kev **freezes
its backbone by design**, so CPT-ing it invalidates the adapter and head and
means rebuilding the decision block against a NORA-specific base rather than
adding to the existing one; and CPT would **bake proprietary spec content into
model weights**, a governance category nobody has ruled on.
**Consequences:** Phase-1 and phase-3 adaptation work is bounded to a LoRA SFT
run on the labelled query set — a day's work, not a research programme, which
is what makes including it in this strand reasonable (§8 open point 4). CPT is
**not rejected in general**: it is the natural remedy for NORA's decision
points that *do* read chunk bodies — reranking, SIRA chunk selection, the
golden-eval Stage-2 judge — where domain language is genuinely the missing
ingredient. That belongs to a successor strand, and the weights-governance
question must be ruled on before it is scheduled.
