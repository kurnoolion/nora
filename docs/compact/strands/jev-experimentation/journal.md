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
