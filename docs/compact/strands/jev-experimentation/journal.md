## 2026-10-13 — Strand opened: category research, egress adjudication, five draft decisions

### Done this session

- **Strand opened** on branch `jev-experimentation` (branch name == strand
  name, matching repo convention). Session bound via `/switch-strand`.
- **Identified what "Jev" is.** TypeSafe AI, released 2026-09-15. A "System
  One" decision model: no token generation, single forward pass, three typed
  output shapes only — `bool`, `score` (graded rubric), `choice`
  (multiple-choice) — each with calibrated probabilities from their RLCD
  (Reinforcement Learning for Calibrated Decisions) training. 64k context (32k
  for `state` plus the longest question); questions in one request are
  evaluated in parallel because no prior context is required. 70-500 ms
  end-to-end, $0.042/M input tokens with output tokens free. One model,
  `jev-1.13.0`, aliased `jev-latest` / `jev-preview`. Text only. Positioned
  explicitly at "classification, routing, filtering, safety checks" — the
  small decisions around generative work.
- **Inventoried NORA's LLM-backed decision points** and mapped them onto the
  three output shapes:
  - `choice` — `_classify_query_type` / `_classify_doc_scope`
    (`core/src/query/analyzer.py`), SIRA chunk selection
  - `score` — `OpenAIRerankChat._score_one` / `_score_batch`
    (`core/src/query/reranker.py:519`, already asks an LLM for an int score),
    golden-eval Stage-2 judge
  - `bool` — refusal detection, query expansion on/off, citation audit
  Three of these are already recorded as *problems* caused by LLM
  non-determinism: taxonomy's 20pp eval swing from graph-topology drift
  (STATUS flag 2026-05-03), the expansion LLM that had to be switched OFF to
  make the `corp-db-layer` identity gate testable, and the Stage-2 judge's
  recorded ±0.5 mean / ±1.7 median noise floor. The category's pitch addresses
  the actual recorded pain, not a hypothetical one.
- **Egress adjudicated → D-DRAFT-1.** Hosted Jev is closed-weights,
  hosted-only, US-only, no VPC/on-prem. Operator confirmed the build/serve
  environment permits no external API calls at all. Hosted Jev is out; the
  target is an open-weights Jev-class model served in-corp.
- **Open-weights landscape surveyed.** A family of self-hostable work-alikes
  exists, several implementing TypeSafe's own `/v1/systemone` HTTP interface:
  Kev (Qwen3.5 0.8-9B, LoRA + pointer head), NanoJev (0.6B Qwen3, explicit
  Choice/Boolean/Score heads), Laya (ModernBERT-large 322-421M,
  non-autoregressive, 100+ languages), Von (395M ModernBERT, OptionMarker),
  SemIf (logits from existing open models, no specialized training), Nimble
  (Qwen3.5-9B + rank-16 LoRA), Rizzo Flow (Spark-X2.5 + llama.cpp). Also
  `razorback16/openjev` (Jev-compatible server on DiffusionGemma) and
  JEV-9B / JEV-27B-VL / GEV-26B-Decide on Hugging Face from AutoTrust AI under
  Apache 2.0. **Caveat: most of this came from a single listicle with licences
  unstated.** Treat as evidence the pattern is reproducible locally, not as a
  vetted shortlist. Repos and licences still need first-hand verification.
- **GEV-26B-Decide characterized** (architect-supplied lead). AutoTrust AI,
  Apache 2.0, ~26B MoE, text+image+video, calibrated probability per option,
  adaptive thinking (thinks only when needed), ~45 ms System-1 forward pass.
  bf16 wants 80 GB+ (51.1 GiB); NVFP4 cuts 66% to 17.1 GiB but still needs
  24 GB+. Ships a pre-configured vLLM serve script. → D-DRAFT-4.
- **Found the GGUF trap → D-DRAFT-3.** Published GGUF builds of these models
  contain no decision component — the decision heads live in separate small
  files that GGUF conversion drops, leaving a plain Gemma with a
  decision-model label that loads and answers and is silently the wrong thing.
  NORA serves through Ollama (GGUF), so the most natural integration path is
  the broken one. Independently confirms the architect's suspicion that the
  current runtime does not support a Jev-class decision model.
- **Read the counter-evidence → D-DRAFT-5.** Small purpose-trained classifiers
  match or beat decision models on trained tasks: 200M DeBERTa at 89.01% /
  54 ms vs Jev 86.35% / 348 ms (prompt injection); 125M at 80.27% / 33 ms
  (content safety). Calibration is softer than advertised — Jev overconfident
  at 0.81 mean confidence vs 0.74 actual accuracy (ECE 0.074); JEV-9B's
  published ECE 0.0007 is measured against the teacher's distributions
  ("closeness to Jev, not accuracy against truth"), 0.0759 independently. Open
  models also trail hosted Jev heavily on reasoning benchmarks (Clef 48.0 vs
  78.3 GPQA Diamond; 65.9 vs 82.7 MMLU-Pro; 73.7 vs 92.9 BBH) — probably not
  binding for short-text intent classification, but it bounds how far the
  pattern generalizes to NORA's other six decision points. The survey's
  selection rule: decision models when you lack labels or categories churn;
  **if you have labels, start with a small classifier.**
- **Confirmed the target taxonomy.** `QueryType` in `core/src/query/schema.py:20`
  carries 8 values: `SINGLE_DOC`, `CROSS_DOC`, `CROSS_MNO_COMPARISON`,
  `RELEASE_DIFF`, `STANDARDS_COMPARISON`, `TRACEABILITY`, `FEATURE_LEVEL`,
  `SUMMARIZE`. The architect's informal list (narrow query / broad query / MNO
  comparison / release comparison / summarization) maps onto it cleanly — an
  earlier session note claiming a mismatch was wrong, traced to reading
  `_classify_query_type` in the rule-based twin rather than the enum. A second
  enum around `schema.py:38-48` was not captured and is phase-1 work.

### Architect directive (recorded verbatim in intent, six phases)

1. Fully understand the current query-classification implementation and its
   class taxonomy.
2. Supply full internal infra capability (DGX Spark; A4000 16 GB VRAM; current
   serving runtime, which is suspected not to support a Jev-class model).
3. Design a decision-model flow replacing today's LLM decision-making.
   Decide whether it runs **before or after query expansion**. Support a
   **selectable roster** of decision models, mirroring the existing LLM
   roster, with legacy as the configurable default.
4. Design experimentation / migration phases: a flag routes the
   decision-making flow down the new path, legacy without it. Run a trial
   stack on new ports with the flag enabled, comparing candidates against
   legacy.
5. Rebuild the production image with the flag default-on once validated.
6. Execute the phases in order without breaking existing behaviour.

Patterned explicitly on the `corp-db-layer` strand — whose dual-emit
(`--index=cell-db`, flat default), trial-stack-on-new-ports, identity gate and
frozen-denominator A/B are the templates to reuse. The selectable-roster
requirement has existing precedent in the landed `llm-model-discovery` /
`llm-roster-deploy` work.

### In progress

- Nothing executing. Strand is at design-input gathering; no code written, no
  implementation approved.

### Next

- **Answer three open questions** (blocking, see Flags).
- **Verify the candidate repos and licences first-hand** before any shortlist
  is treated as real. Highest-interest lead: **SemIf**, which extracts logits
  from existing open models with no specialized training — NORA already
  self-hosts Ollama and qwen3, so constrained-choice decoding over a model
  already inside the network could deliver typed calibrated decisions with
  zero new serving infrastructure, sidestepping both D-DRAFT-3 (vLLM) and
  D-DRAFT-4 (DGX Spark).
- **Build the measurement instrument.** ~40-60 hand-labelled queries →
  expected `QueryType` + `DocTypeScope`. The frozen 39-sample set (digest
  `29088f2a4167`) could not separate bm25x from fts5 in the `corp-db-layer`
  engine A/B, so it will not separate a decider from an LLM either; without
  new labels this experiment can only return an underpowered null. Candidate
  label sources: recorded user questions from the `ask-history` submit-time
  recording, the 18-Q OA eval set, synthetic queries per class.
- **Four-arm comparison** on that set: `MockQueryAnalyzer` (rules) vs
  `LLMQueryAnalyzer` (current) vs a decision-model candidate vs a small
  purpose-trained classifier. Measure per-class accuracy, determinism across
  repeated runs, and latency.
- **Then** design the flag-gated flow and roster per directive phases 3-4.
  Architectural — needs its own brainstorm and a committed design spec before
  any implementation.

### Flags

- **Three dictation-garbled terms need confirming with the architect** before
  phase 1 can be scoped: (a) the current **serving runtime** name — it
  determines whether the D-DRAFT-3 GGUF trap is live or moot; (b) the
  production **image / stack name** referenced for the phase-5 rebuild; (c)
  the exact **DGX Spark specification**, since directive phase 2 asks for full
  infra capability and only fragments are recorded.
- **The directive assumes the category delivers.** D-DRAFT-5's counter-evidence
  means phase 1 can legitimately end at "use the rule-based analyzer you
  already have, or train a 200M classifier" with phases 2-6 never happening.
  That outcome needs to be acceptable to the architect *before* the
  measurement runs, or the experiment is decoration.
- **A query-time dependency on DGX Spark** (if GEV-26B wins) introduces
  contention and a single point of failure on the interactive Ask path. Not
  evaluated yet.
- **vLLM is new operational surface** for NORA. Cost belongs to this strand,
  not to whichever phase first notices it.
- **Any candidate evaluation must assert the decision heads are actually
  loaded** before recording an accuracy number — per D-DRAFT-3, silent
  degradation to base-model token generation would invalidate a whole
  comparison without erroring.

### Sources

- TypeSafe Jev docs — https://docs.typesafe.ai/models
- Jev enterprise security review — https://neuraltrust.ai/blog/jev-typesafe-ai-security
- GEV-26B-Decide — https://huggingface.co/autotrust/GEV-26B-Decide and https://huggingface.co/autotrust/GEV-26B-Decide-NVFP4
- Open Jev models, hardware, the GGUF trap — https://www.logeshwaran.org/2026/10/open-source-jev-models-jev-27b-gev-26b.html
- Open-source alternatives survey — https://www.datacamp.com/blog/top-open-source-jev-alternatives
- Counter-evidence survey — https://redreamality.com/blog/open-decision-models-2026-survey-benchmarks/
- openjev reference server — https://github.com/razorback16/openjev
- GEV-26B adaptive thinking (architect-supplied; 403 to automated fetch, not read first-hand) — https://medium.com/data-science-in-your-pocket/gev-26b-a-jev-like-decision-model-with-adaptive-thinking-4ed75c09bbd8

## 2026-10-13 (cont.) — Objective set, hardware scoped, a live analyzer defect found

### Done this session

- **Objective reaffirmed and recorded → D-DRAFT-6.** The strand ships a
  configurable Jev-class decision path and explores its effectiveness; the
  deliverable is working integration plus in-house expertise, not a proven
  accuracy win. Decision models are fast and gaining adoption, the expertise
  transfers to NORA's six other decision points and to classification work
  beyond NORA, and the integration cost is bounded because the seam is one
  factory function. Proceeds irrespective of how baselines compare.
- **D-DRAFT-5 reframed accordingly.** The rule-based and small-classifier arms
  are reference baselines measured for accountability — mandatory, because
  without them the experiment yields an unanchored number — but they do not
  gate the exploration. "The baseline won" is a successful outcome of this
  strand. Reporting must keep three questions separate: does the decision path
  work, is it faster, is it more accurate. Legitimate answers: yes / yes / no.
- **Hardware scoped → D-DRAFT-4 amended.** DGX Spark is currently committed as
  the backup host for LLM synthesis — contended, likely unavailable near-term,
  explicitly not ruled out but not plannable. The A4000 (16 GB) is the
  practical target and can also act as backup. Consequence: **GEV-26B-Decide
  is out of scope for iteration 1** (17.1 GiB NVFP4 weights exceed 16 GB
  before context) and re-enters only if DGX Spark frees up. First-iteration
  shortlist is the small end: ModernBERT-class (322-421M), sub-9B Qwen3.5
  builds, and SemIf-style constrained decoding over an already-resident model.
  Favourable side effect: these fit the A4000 comfortably and some run on CPU.
- **`QueryType` fully enumerated — ten members, not eight.** `SINGLE_DOC`,
  `CROSS_DOC`, `CROSS_MNO_COMPARISON`, `RELEASE_DIFF`,
  `STANDARDS_COMPARISON`, `TRACEABILITY`, `FEATURE_LEVEL`, `SUMMARIZE`,
  `FACT`, `GENERAL`. `SUMMARIZE` and `FACT` are the intent-shaped types added
  in "Step 4", each gating its own per-type knobs.
- **Live defect found at the target seam.** `MockQueryAnalyzer` emits `FACT`
  (`analyzer.py:269`) and `SUMMARIZE` (`:283`), but `LLMQueryAnalyzer`'s prompt
  enumerates only 8 `query_type` options and omits both. So whenever the LLM
  analyzer is active, those two classes are unreachable and their routing
  knobs are silently dead — wide `top_k` + grouping-disabled for `SUMMARIZE`,
  tight `top_k` + strict `max_distance_threshold` + rerank-ON for `FACT`.
  Needs its own fix regardless of this strand's outcome; it also predicts that
  any legacy-vs-decision-path A/B will show class-distribution differences
  that are a prompt defect, not a model effect.
- **Analyzer seam partitioned → D-DRAFT-7.** `QueryIntent` carries nine
  populated fields, so the decision model cannot be a drop-in replacement for
  `analyze()`. Partition by output shape: decision model owns `query_type`
  (10-way choice) and `doc_type_scope` (3-way choice), optionally `mnos` /
  `likely_features` / `plan_ids` as parallel per-candidate booleans — which is
  precisely the one-state-many-questions pattern the category is built for.
  Open-extraction fields (`entities`, `concepts`, `releases`,
  `standards_refs`) stay on `MockQueryAnalyzer`'s existing regex extractors,
  which are already written and tested. The result is a hybrid analyzer, and
  it is architecturally cleaner than today's single LLM call doing
  classification and extraction together with a wholesale fallback on any JSON
  parse failure.

### Next

- Open design question for the architect: decision model **before or after
  query expansion** (directive phase 3). Not yet analysed.
- Candidate repo + licence verification first-hand, small end of the field.
- Labelled query set (~40-60) for the four-arm comparison.
- Then the architectural design: hybrid analyzer, roster + flag shape
  (mirroring the landed LLM roster), trial stack on new ports per
  `corp-db-layer`'s template.

### Flags

- **`SUMMARIZE` / `FACT` unreachable under `LLMQueryAnalyzer`** — standalone
  defect, independent of this strand. Fix before any legacy-vs-decision A/B,
  or the baseline arm is measured against a broken legacy path.
- Three architect questions still open: serving runtime name, production
  image/stack name, exact DGX Spark specification.

## 2026-10-13 (cont. 2) — Runtime confirmed, placement settled, interface chosen

### Done this session

- **Serving runtime confirmed: both Ollama and vLLM are in production.** This
  is the best case and closes the first of the three open architect questions.
  Consequence: the D-DRAFT-3 GGUF trap is avoidable by construction rather
  than by discipline — the legacy LLM path stays on Ollama untouched, the
  decision path runs on vLLM, and the two arms of the A/B are cleanly isolated
  with no contention on the legacy default. The main infra objection to
  head-bearing decision models is therefore already paid for.
- **Placement settled → D-DRAFT-8.** Directive phase 3's before-or-after
  question is foreclosed by existing code: `pipeline.py:500` gates expansion
  on `_TYPE_REWRITE_ENABLED.get(intent.query_type, False)`, with `analyze()`
  at `:420` and `rewrite()` at `:501`. Expansion is a consequence of
  classification, so "after" would leave nothing to decide whether to expand.
  Every other per-type knob (top_k, BM25 weight, rerank, threshold, grouping,
  system prompt) has the same dependency.
- **Derived measurement requirement.** Because classification sits at the head
  of the interactive path, a misclassification propagates into every
  downstream knob. Per-class accuracy — a full confusion matrix over all 10
  `QueryType` values — is therefore required output, not optional. Worked
  example of why overall accuracy is insufficient: a candidate at 90% overall
  that systematically confuses `FACT` with `SUMMARIZE` inverts two opposed
  retrieval configurations and could be worse in practice than the rule-based
  baseline.
- **Interface chosen → D-DRAFT-9.** `/v1/systemone` becomes NORA's internal
  decision-provider wire format, behind a `DecisionProvider` protocol, with a
  roster selecting among providers and legacy as the configurable default.
  Direct precedent: `OpenAICompatibleProvider` speaks `/v1/chat/completions`
  over stdlib urllib with no SDK dependency, which is what made the LLM roster
  vendor-neutral and let `llm-model-discovery` add model selection without
  touching providers. Candidates without a `/v1/systemone` endpoint
  (ModernBERT-class, SemIf-style) get a thin in-process adapter implementing
  the same protocol.

### Next

- Decide module placement: new `core/src/decision/` package vs extending
  `core/src/llm/`. Needs a `MODULE.md` either way, shipped in this branch.
- Pick the iteration-1 lead candidate now that vLLM is available — the small
  end with real decision heads (NanoJev 0.6B, Kev) is reachable.
- Labelled query set (~40-60) across all 10 classes for the four-arm
  confusion-matrix comparison.
- Then the full architectural design + committed spec before any code.

### Flags

- Two architect questions still open: production image/stack name (for the
  phase-5 default-on rebuild) and the exact DGX Spark specification.

## 2026-10-13 (cont. 3) — Shortlist verified first-hand; Kev selected

### Done this session

- **Shortlist verified** against repos rather than the earlier single listing.
  - **Kev** — family of 0.5B-9B decision models on Qwen3.5. Each checkpoint is
    a rank-16 LoRA adapter plus a small bilinear pointer head; the readout
    scores each option's hidden state against the question's `<decide>` hidden
    state and softmaxes. Kev-0.8B on Qwen3.5-0.8B-Base, Kev-4B on
    Qwen3.5-4B-Base, both Apache-2.0 bases. Implements TypeSafe's
    `/v1/systemone`, so their Python SDK can point at a local server. Actively
    developed; ecosystem support landing (vllm.cpp PR, system1-omni issue).
    **Explicitly trainable on own data.**
  - **openjev** — Apache-2.0, DiffusionGemma 26B-A4B (discrete diffusion,
    denoises full token canvases rather than left-to-right). Native
    `/v1/systemone` plus OpenAI `/v1/decisions` compatibility; bool/choice/
    score all supported; confidence defined as `1 - H(p)/ln K`. 698 stars,
    67 commits. vLLM backend needs **24 GB+**; MLX backend ~16 GB on Apple
    silicon. 30 ms p50 for short states on an RTX PRO 6000, degrading to 4.8 s
    at 64k context; up to 64 concurrent reads. Decision heads are integrated
    into the weights rather than separate files.
- **Kev selected as iteration-1 lead → D-DRAFT-10.** It is the only verified
  native-`/v1/systemone` option that fits the A4000, and it validates the
  D-DRAFT-9 protocol choice directly rather than through an adapter.
- **Unanticipated finding: Kev's trainability collapses the D-DRAFT-5
  tension.** The counter-evidence held that a small purpose-trained classifier
  beats a generic decision model on trained tasks. Kev being LoRA-trainable
  means a Kev fine-tuned on NORA's labelled queries *is* a small
  purpose-trained classifier — with calibrated typed outputs and the decision
  interface. The baseline arm and the decision arm therefore merge in
  iteration 2 instead of competing, which is a materially better outcome than
  either branch of the original framing.
- **openjev and GEV-26B parked on VRAM**, both needing 24 GB+. Re-enter if DGX
  Spark frees up from its LLM-synthesis backup duty.

### Flags

- **Supply chain: establish the canonical Kev upstream before pulling anything.**
  Searches surface many near-identical `kev` repositories (`jaredpalmer`,
  `chi2liu`, `song007`, `Radexito`, others) with no obvious provenance
  ordering. Iteration 1 brings external model code and weights into the
  corporate network; this is a review item for whoever owns software intake,
  not a formality.
- **No published accuracy or calibration benchmarks exist** for Kev or
  openjev. NORA's own labelled set will be the only evidence for this choice,
  which raises its priority from "needed for the comparison" to "needed for
  the decision to be defensible at all".
- Two architect questions still open: production image/stack name, exact DGX
  Spark specification.

## 2026-10-13 (cont. 4) — Design doc written to the house convention

### Done this session

- **Artifact location settled empirically.** Superpowers' default spec path
  (`docs/superpowers/specs/`) does not exist in this repo and never has.
  Seven strands instead keep design docs **inside the strand folder** as
  `<topic>-design.md` (`corp-db-layer/storage-design.md`,
  `corp-db-layer/converter-design.md`, `_archive/mno-c-ingestion/
  asset-ingestion-design.md`, `_archive/sira-enrichment-pe/
  enrichment-pe-design.md`, `_archive/multi-mno-nora/
  multi-mno-ingestion-design.md`, `_archive/docker-distro/
  docker-distro-design.md`, `_archive/sira-enrichment-review/
  enrichment-review-design.md`). The split adopted: **Superpowers owns the
  process** (brainstorm → design → plan → execute), **COMPACT owns the
  artifacts** (in-strand design doc, journal, decisions-draft, promotion via
  `/land-strand`). One set of files, in the places the architect authored. The
  brainstorming skill explicitly permits this — its own text defers spec
  location to user preference.
- **`decision-model-design.md` written** (draft v0.1), matching
  `storage-design.md`'s format: title + strand/version/status byline,
  cumulative parenthetical version log, `Inputs:` block naming sources and
  architect rulings with dates, numbered sections, `## 8. Open points for
  adjudication` last. Sections: shape of the answer · the picture (seam
  diagram + downstream knob fan-out) · `QueryIntent` partition by output
  shape · the code seam and `/v1/systemone` · candidates and hardware · five
  gated phases · measurement · invariants · open points.
- **Prior-art warning folded into the design.** NORA already ships a
  head-bearing model: `CrossEncoderReranker` (`reranker.py:90`,
  `ms-marco-MiniLM-L6-v2`) is the same architecture class with a scoring head
  instead of a decision head. The A11 result had it net-zero and slightly
  regressive (86.4% / 62.3%), diagnosed as out-of-distribution on telecom spec
  text. A generic Kev should be expected to hit the same wall for the same
  reason, which makes the LoRA fine-tune the load-bearing step rather than an
  optional extra. Recorded as §4's warning and §8's open point 3.
- **Spec self-review run.** Two fixes applied: the fourth measurement arm
  (fine-tuned Kev) was stated as definite while §8 still asked whether
  fine-tuning is in scope — now explicitly conditional; and "phase" versus
  "iteration" were used interchangeably — now defined once in §5.

### Next

- Architect review of `decision-model-design.md`, particularly §8's six open
  points.
- On approval: `superpowers:writing-plans` for the phase-1 implementation plan
  (defect fix + labelled set + baseline measurement). No code before that.

### Flags

- **Six open points carried in the design doc** awaiting adjudication: module
  placement; canonical Kev upstream (supply-chain review before anything is
  pulled); fine-tuning budget; ownership of the `SUMMARIZE`/`FACT` defect fix;
  the two outstanding architect questions; and phase-5 default-flip criteria.
- **Phase-5 criteria unstated is the one worth closing early.** Agreeing now
  what measurement justifies flipping the default prevents the decision being
  relitigated later against a moving bar — the same failure the strand's
  objective framing (D-DRAFT-6) is meant to prevent.
