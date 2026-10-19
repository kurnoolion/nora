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

## 2026-10-13 (cont. 5) — Design claims verified; defect blast radius is larger than first written

### Done this session

- **Three load-bearing design claims verified rather than assumed**, after a
  review pass flagged that all three were written as fact without being read:
  - `make_query_analyzer()` (`analyzer.py:415`) confirmed as a two-branch
    factory on a single `llm_provider=None` argument. So the decision path
    needs a **signature extension**, not just a third branch — folded into
    design §3. Its docstring also records the governing posture from
    `multi-mno-sira` D-DRAFT-9 ("the same configured-LLM-or-mock posture as
    the rest of NORA"), which the decision path should mirror: configured
    decision provider → hybrid, otherwise fall through to today's two branches
    unchanged. That fall-through is what makes the phase-2 identity gate
    achievable.
  - `_FEATURE_KEYWORDS` counted at **16** keys. Full phase-3 shape is
    16 + 3 + 5 = 24 booleans plus 2 choices = 26 questions against one state —
    comfortably inside documented concurrency (openjev advertises 64
    concurrent reads). Design §2 now states the count instead of leaving the
    cardinality open.
  - **Per-type map key sets read — the defect is worse than first written.**
    `_TYPE_MAX_DISTANCE` (`:104`) contains exactly two entries, `FACT: 0.4`
    and `SUMMARIZE: 0.7`. `_TYPE_DISABLE_GROUPING` (`:199`) contains exactly
    one, `SUMMARIZE`. Since neither class is reachable under
    `LLMQueryAnalyzer`, **both features are entirely inert on that path**, not
    merely degraded. `_TYPE_REWRITE_ENABLED` loses only its `SUMMARIZE` entry.
    An earlier grep missed this by searching lowercase string literals when
    the maps reference the enum.
- **Defect ownership claimed → D-DRAFT-11.** Removed from the design's open
  points; it is this strand's phase-1 work, not an adjudication question. It
  is a prompt-string edit plus a regression test, it was found here, and it
  gates the phase-1 baseline.
- **Design doc bumped to v0.2** with all of the above folded in: §2.1 now
  carries a verified blast-radius table, §2 states the counted cardinality, §3
  carries the factory signature finding and the D-DRAFT-9 posture, §8 is down
  to five genuinely architect-level open points.

### Flags

- **NORA's historical eval baselines may all have been run with the defect
  live.** A4 (88.0% / 67.6%), A8 (88.3% / 64.3%) and A11 (86.4% / 62.3%) were
  produced with a configured LLM provider, which routes through
  `LLMQueryAnalyzer`. If so, the per-type distance thresholds and
  grouping-disable were inert for every one of those runs, and the tuned
  0.4 / 0.7 values have never taken effect in any recorded baseline. Needs
  confirming. If confirmed, the phase-1 fix is a behaviour change to the
  legacy path in its own right and deserves its own before/after measurement,
  independent of the decision-model work — and it may partly explain the
  `SUMMARIZE`-shaped retrieval complaints carried in
  `nora-retrieval-quality`.

## 2026-10-13 (cont. 6) — Design v0.3: head-bearing framing corrected

### Done this session

- **Corrected a misreading the design invited.** v0.2's §4 "prior art warning"
  about `CrossEncoderReranker` read as an argument *against* head-bearing
  models. It is not, and the design does not reject them — **Kev is a
  head-bearing model**, as is hosted Jev, and the head is the mechanism that
  makes the whole category fast. Split into two sections:
  - **§4.1 — head-bearing is the speed mechanism.** A generative LLM is slow
    because it is autoregressive: N output tokens cost N serial forward
    passes. A decision head removes the loop — one forward pass, then a thin
    trained layer reads the final hidden state and emits a distribution over
    the K options directly. Zero tokens generated; latency independent of K.
    Kev's bilinear pointer head scores each option's hidden state against the
    question's `<decide>` hidden state; openjev states the same property as
    "one read generates label token probabilities directly from model
    outputs". Base size still sets the cost of that single pass, so Kev-0.8B
    locally should **beat** hosted Jev's quoted 70-500 ms — smaller base, no
    network hop. Self-hosting small improves the category's speed claim rather
    than surrendering it.
  - **§4.2 — prior-art warning scoped to accuracy.** The A11 lesson is about
    generic weights being out-of-distribution on telecom spec text and implies
    nothing about architecture or latency. A generic Kev should be expected to
    hit the same wall, which is the argument for budgeting the LoRA fine-tune.
- **A-series labels identified in the doc.** A3 / A4 / A6 / A7 / A8 / A11 are
  NORA's **own** labelled eval runs recorded in `STATUS.md` against the 18-Q
  OA eval set — not external benchmarks. A4 88.0% / 67.6% (qwen3-235b-a22b via
  OpenRouter), A7 87%, A8 88.3% / 64.3% (the deterministic baseline, whose
  3-run variance experiment showed a 6.7pp spread), A11 86.4% / 62.3% (the
  cross-encoder run), A3 81.7% / 54.6% (gemma4:e4b), and A4-vs-A6 the 20pp
  graph-topology gap. The doc now says so inline so a reader does not have to
  reconstruct it.

## 2026-10-13 (cont. 7) — AutoTrust family enumerated; openjev blocked on licence

### Done this session

- **Terminology settled with the architect-side operator.** Decision model =
  head-bearing model = System One model — same thing. Jev is TypeSafe's hosted
  closed product; the open models are alternatives. One sharpening recorded:
  "classification model" is narrower than the rest, since the category emits
  `choice` (classification) **plus** `score` and `bool`.
- **AutoTrust AI family enumerated** (Singapore applied-AI lab,
  `huggingface.co/autotrust`), all Apache 2.0 — and **none of it fits the
  A4000's 16 GB**, so the whole family is DGX-Spark-or-nothing and Kev remains
  the iteration-1 pick:
  - **JEV-9B** — Qwen3.5-9B backbone, ~18 GB download, 24 GB VRAM minimum
    (32-48 comfortable), vision added 2026-10-03. AutoTrust's first integrated
    System 1 + System 2 model.
  - **JEV-27B** — Qwen3.8-27B, ~54 GB, 80 GB+ VRAM, text only. Keeps **96% of
    hosted Jev's accuracy at 16 options**, KL divergence ~0.017.
  - **JEV-27B-VL** — Qwen3.8-27B + vision, 256k inputs, 80 GB+ full; Q8_0
    ~48 GB at 99.0% agreement with full precision, Q4_K_M ~24 GB at 92.9%.
    Hit #1 on Hugging Face's global trending list 2026-10-08; leads the Jev
    Decision Index vision board at 69.82 against 67.66 for the next entrant.
  - **GEV-26B-Decide** — Gemma 4 26B-A4B MoE (~4B active), ~49.5 GB full,
    NVFP4 18 GB / 17.1 GiB weights needing 24 GB short-context.
- **"Blocks of Experts" recipe recorded → design §4.0.** The backbone stays
  **frozen**; a small **detachable** decision block is added. JEV-9B's System 1
  block is **40.2M trained parameters, ~0.5% of its backbone**. This single
  fact explains three things at once: the GGUF trap's mechanism (a detachable
  block ships as separate files), why the block costs nothing in generative
  quality (JEV-9B scores 70.7% HumanEval *identical* to base Qwen3.5-9B,
  backbone untouched), and why the §4.2 fine-tune is affordable rather than a
  research programme. Kev's rank-16 LoRA + pointer head is the same idea at
  smaller scale.
- **System 1 vs System 2 scoped.** System 1 = typed question, one forward
  pass, calibrated distribution. System 2 = ordinary generation with
  step-by-step reasoning. GEV's adaptive thinking switches between them
  (reportedly Minesweeper 21.0 → 86.0, Connect Four 54.0 → 99.5). NORA's
  phase-1 use is **System 1 only**; adaptive thinking is a latency risk on the
  interactive path and is explicitly out of scope until measured.
- **GGUF detection made concrete → design §7.** AutoTrust's own guidance: the
  file list must contain `head.safetensors`, an adapter folder, and
  `calibration.json`. **A hollow distribution contains only `.gguf` files.**
  What a stripped build omits is the decision adapter, the 24-slot decision
  head, and the calibration temperatures. Phase 3 now asserts this in code
  rather than by inspection. One known working exception exists: Atlas3D's
  JEV-27B-VL ships a `llama.cpp-jev.patch`, because the `jev` decision type is
  absent from upstream llama.cpp.
- **Small-model field widened** (the bucket that actually fits): Kev-4B, Tev1
  (0.8B, 812 MB), Nimble, Lev, Laya, Julia-1. Licences and `/v1/systemone`
  support unverified for all but Kev; added to the design as roster candidates
  pending verification.

### Flags

- **openjev is blocked on a licence conflict.** Its GitHub repository states
  Apache-2.0; a secondary survey records openjev (27B) as **CC BY-NC 4.0,
  commercial use prohibited**. The sources also disagree on the model itself
  (DiffusionGemma 26B-A4B on vLLM/MLX versus a 27B served through llama.cpp).
  An NC licence is a hard blocker for corporate deployment. Deliberately not
  resolved here — legal confirms from the repository itself. Recorded as
  design §8 open point 2.
- **Correction to an earlier flag:** published benchmarks for this category
  *do* exist — the **Jev Decision Index**, with a vision board. The earlier
  "no published benchmarks" note was accurate only for Kev and openjev
  specifically, not for the category.
- **The convenience path is the trap path.** The small models that fit the
  A4000 are the ones distributed for Ollama / llama.cpp — i.e. via GGUF, the
  exact format that silently drops decision blocks. Every small candidate
  needs the §7 file-list check before it is believed, and the "runs on a
  laptop" framing in secondary sources should be treated as unverified for
  decision capability.

## 2026-10-13 (cont. 8) — NC licence scope clarified

### Done this session

- **Clarified that "non-commercial" does not exempt internal corporate use**
  (design §8.2, v0.5). CC BY-NC 4.0 turns on whether use is "primarily
  intended for or directed towards commercial advantage" — a **purpose** test,
  not a distribution test. A for-profit company running a model internally in
  support of its commercial business is generally commercial use; keeping it
  inside the network changes nothing. Compounding factors: Creative Commons
  does not recommend CC licences for software and concedes NC is ambiguous,
  and the copyright status of model weights and fine-tuned derivatives is
  unsettled. Determination is fact- and jurisdiction-specific, so it belongs
  with legal.
- **Recommendation recorded: drop openjev rather than adjudicate it.** It is
  already parked on VRAM (does not fit the A4000), so the licence question
  costs nothing to avoid. Revisit only if DGX Spark frees up and openjev is
  actually wanted.
- **New intake item: Kev's adapter/head licence is unverified.** The Qwen3.5
  *bases* are Apache-2.0, but the licence covering Kev's own rank-16 LoRA
  adapter and bilinear pointer head is a separate artifact. Base licence does
  not imply adapter licence. Folded into the canonical-upstream review
  (§8.3), which must now confirm provenance **and** licence for the adapter
  itself before anything is pulled.

## 2026-10-13 (cont. 9) — Iteration-1 candidate locked: Kev-4B, as-is

### Done this session

- **Kev-4B on the A4000, unmodified, selected for iteration 1 → D-DRAFT-12.**
  No quantization, no AWQ conversion, no DGX Spark dependency. Rationale: it
  runs out of the box on hardware already in place with a runtime already in
  production, so iteration 1 spends its effort on the seam, the labelled set
  and the measurement instead of model engineering. JEV-9B-AWQ remains in the
  roster as a later entry.
- **Hardware rule recorded in plain terms** for whoever picks this up: small
  models fit the A4000 as-is; large models (JEV-9B, JEV-27B, JEV-27B-VL,
  GEV-26B-Decide) fit DGX Spark as-is; anything in between needs shrinking,
  and shrinking must use AWQ (keeps the decision block) rather than GGUF
  (drops it). Caveat attached: DGX Spark's unified memory will fit the large
  models but is slower than dedicated GPU memory, so "fits" is not "fast"
  there until measured.
- **Accuracy expectation set explicitly.** A generic 4B decision model on
  telecom spec text will likely score modestly, per the §4.2
  out-of-distribution warning and NORA's own A11 precedent. This is accepted
  up front rather than discovered later — the iteration-1 deliverable is a
  working configurable path plus honest measurement, not a win (D-DRAFT-6).

## 2026-10-13 (cont. 10) — Kev cleared; model card corrects three design assumptions

### Done this session

- **Both intake questions resolved first-hand → D-DRAFT-13.** Canonical
  upstream is **`jaredpalmer/kev`** (original, no fork marker, 9.1k stars, 343
  commits); the several other `kev` repositories are forks of it.
  **Apache-2.0 covers the repository, the LoRA adapter and the pointer head**,
  and the Qwen3 / Qwen3.5 / Qwen3.8 bases are Apache-2.0 as well. Nothing
  ambiguous, commercial use permitted. Design §8 open points 2 and 3 close.
  Kev 1.0 shipped Kev-0.8B / 4B / 9B on Qwen3.5 and Kev-27B on Qwen3.8;
  weights at `huggingface.co/collections/jaredpalmer/kev`. Minor item for pull
  time: the `kev-4b-qwen3` card cites `Qwen/Qwen3-4B-Base` while the repo
  overview says Qwen3.5-4B-Base, so both variants may exist.
- **Correction 1 — Kev does not use vLLM.** It ships its own server exposing
  `/v1/systemone`: `python -m kev.serve --run jaredpalmer/kev-4b --port 8008`.
  vLLM support is in flight (a `vllm.cpp` PR) but has not landed. Simpler than
  planned rather than harder — one self-contained Python server, no vLLM
  plumbing for iteration 1. The GGUF ban still binds.
- **Correction 2 — bf16 is mandatory on the A4000.** "4B fp32 needs ~16 GB"
  exactly saturates the card with nothing left for context; `KEV_DTYPE=bf16`
  roughly halves it to ~8 GB. A flag that must be set, not a default.
- **Correction 3 — generic Kev-4B will be weak and uncalibrated on this
  corpus, per its own card.** In-distribution 0.854, **out-of-domain 0.790**,
  OOD Brier 0.328, **ECE 0.096 described as uncalibrated out of domain**;
  locked test 0.856 / 0.806. Latency ~45 ms on an H100, so slower on an
  A4000. Training suite is ~13,576 generic text-classification records over
  two epochs (banking77, BoolQ, AG News, MultiNLI, SST5, Yelp, TREC, DBpedia,
  Amazon Reviews, IMDB) — nothing like telecom requirement specs. The card
  states outright that "product-shaped questions with no training analogue are
  not guaranteed", which is precisely NORA's `QueryType` task.
- **Consequence recorded: the fine-tune is the experiment, not an extra.**
  The model's own documentation now confirms the §4.2 OOD warning that was
  previously inferred from NORA's A11 cross-encoder result, and it retires
  out-of-the-box calibration — the category's headline benefit — for this use
  case. Encouragingly, 13.5k records over two epochs is a small training job,
  which argues for resolving §8's fine-tuning-budget question in favour of
  keeping it inside this strand.

## 2026-10-13 (cont. 11) — Terminology fixed; domain-adaptation idea noted

### Terminology, for the record

Loose use of "trained" was corrected in discussion. Precisely:

- **Pre-training** — from scratch, next-token prediction, huge corpora. No labels.
- **CPT (continued pre-training)** — next-token prediction continued on *domain*
  text. Teaches the model the domain's **language**. No labels needed.
- **SFT (supervised fine-tuning)** — training on input→output pairs. Teaches
  the model a **task**. Labels required.
- **Fine-tuning** — umbrella term; CPT and SFT are both kinds of it.

**What Kev did:** trained a LoRA adapter (r=16) plus a newly-initialised
pointer head on 13,576 **labelled** classification records with the backbone
**frozen** — i.e. parameter-efficient **SFT**, not CPT. Both verbs circulate
legitimately because the *head* is new and genuinely trained from scratch,
while the *adapter* sits on a pretrained base and is therefore fine-tuned. For
what NORA would do to Kev, **"fine-tune" is the correct term**.

### Noted idea (architect-side): domain-adapt the base on NORA's own corpora

Proposal: CPT ("season") the base model on telecom requirement specs — and
procurement specs, if those are in scope; the term needs confirming — before
task fine-tuning. Assessment, recorded rather than actioned:

**Where it does not help: phase 1.** The query classifier reads **user
questions**, not corpus text — "What are all the data-retry requirements?",
"Compare VZW and TMO on IMS registration", "What changed in eSIM from Oct 2025
to Feb 2026?". What separates `RELEASE_DIFF` from `TRACEABILITY` is **question
shape**, not telecom vocabulary. CPT on dense spec prose teaches the backbone
the language of *documents*, a different distribution from the language of
*questions about* documents, and could plausibly hurt by pulling the
representation toward text never seen at inference. For this task, **SFT on
labelled queries is the direct and likely sufficient fix**.

**Where it is genuinely strong: the other decision points.** Reranking, SIRA
chunk selection and the golden-eval Stage-2 judge all read **chunk bodies** —
real spec text. There the missing ingredient *is* domain language, and CPT
would be operating on the right distribution. This is the natural home for the
idea, and it belongs to a successor strand rather than this one.

**Two blockers to weigh before CPT becomes a plan:**

1. **Kev freezes its backbone by design.** CPT-ing the backbone invalidates the
   adapter and head trained against the original — it means rebuilding Kev's
   decision block on a NORA-specific base, which is substantially more work
   than a LoRA run, not an add-on to one.
2. **CPT bakes proprietary spec content into model weights.** The standing
   project rule keeps proprietary content out of logs, error messages, compact
   reports and test fixtures. **Model weights are a category nobody has ruled
   on**, and weights can leak training data. This needs a governance decision
   before any CPT work is scheduled — flagged, not assumed.

### Flags

- **Governance: proprietary content in model weights is unruled.** If
  domain-adaptive pre-training on NORA's corpora is ever pursued, someone must
  rule on whether spec content may enter trained weights, and what that implies
  for where those weights may be stored, served, or shared. Distinct from the
  egress question already settled in D-DRAFT-1.
- "Procurement spec" from the dictated proposal needs confirming — NORA's
  corpus is device requirement specifications; whether procurement documents
  are a separate in-scope corpus is unclear.

- **CPT rejected for query classification, confirmed with the architect-side
  operator → D-DRAFT-14.** Agreed assessment: the target is "mostly pure
  English", so SFT on labelled queries is both the simpler and the
  better-aimed intervention; no need to overdo it. CPT stays available as the
  remedy for the chunk-body-reading decision points in a successor strand,
  subject to the weights-governance flag above.

## 2026-10-13 (cont. 12) — CORRECTION: classification is rule-based; the defect is latent

### Done this session

- **Checked the analyzer's call sites, which earlier sessions had not.**
  `LLMQueryAnalyzer` is **never constructed in any live path**:
  `core/src/query/pipeline.py:290` is `self._analyzer = analyzer or
  MockQueryAnalyzer()`; `sandbox/sira_query/service.py:1191` is
  `make_query_analyzer(None)`, with a docstring note recording that wiring an
  `LLMProvider` for `LLMQueryAnalyzer` is a deliberate follow-up. Outside
  tests nothing instantiates it. **Query classification in the serving path is
  100% rule-based today.**
- **Corrections issued → D-DRAFT-15:**
  - The `SUMMARIZE`/`FACT` defect is **latent, not live**. `MockQueryAnalyzer`
    emits both classes, so `_TYPE_MAX_DISTANCE` and `_TYPE_DISABLE_GROUPING`
    fire normally and the tuned 0.4 / 0.7 thresholds do take effect.
  - **The A4 / A8 / A11 flag is withdrawn.** Those runs used the rule-based
    analyzer; the baselines are sound.
  - The defect is demoted from phase-1 gate to landmine — fix before anyone
    enables the LLM analyzer, but it blocks nothing now.
  - The measurement loses an arm: the "current LLM" arm does not exist in
    production. Real arms are rules (live), decision model, fine-tuned
    decision model.
  - **Premise reframed:** this seam has **no LLM call to displace**. A
    decision model here replaces *rules*. D-DRAFT-2's selection reasons still
    hold, but no LLM cost is saved here. The LLM-spending decision points are
    reranking, SIRA chunk selection and the Stage-2 judge — all chunk-body
    readers. If token cost rather than capability is the driver, target
    ordering should be revisited with the architect.
- **Process lesson recorded:** verifying what a thing *contains* is not
  verifying whether anything *uses* it. The per-type maps were checked for
  their key sets; the analyzer's call sites were not, and the blast-radius
  claim was asserted on that gap.

### Reference — the live classifier, for the architect's review

Ordered phrase matching over 10 `QueryType` values plus 3 `DocTypeScope`
values. Precedence is deliberate and documented in comments: `FACT` is checked
before definitional patterns so "what is the maximum X" is not mis-routed;
`STANDARDS_COMPARISON` precedes `RELEASE_DIFF` because "differ" overlaps
"diff"; `SUMMARIZE` precedes entity/breadth heuristics so "explain the X
authentication requirements" routes to a wide survey rather than a narrow
lookup. Signals include MNO count (>=2 → `CROSS_MNO_COMPARISON`), release
count, standards references, and phrase sets such as
("value of", "how many", "how long", "default value", "maximum value",
"what is the threshold") → `FACT` and ("explain ", "summarize", "overview of",
"describe ", "tell me about ") → `SUMMARIZE`. It works because the user
population asks in formulaic shapes; it is brittle to unanticipated phrasing,
which is the gap a trained classifier would target.

## 2026-10-13 (cont. 13) — Placement confirmed live; latency accepted; a missing record found

### Done this session

- **Placement re-verified on the LIVE path → D-DRAFT-17.** D-DRAFT-8 had
  argued from `core/src/query/pipeline.py` (the `nora` lane). Applying the
  D-DRAFT-15 lesson, the `sira` lane was checked: `analyze()` is step 1 of
  `_multi_cell_query` (`service.py:1191`) and expansion happens later inside
  retrieval (`filter_query_expansion` / `search_with_expansion`,
  `:1010`-`:1029`). **Classification precedes expansion in both lanes**;
  current behaviour is preserved, as the architect-side operator preferred.
  Terminology recorded: the service calls that stage "query **enrichment**"
  (`:559`) while the core lane calls it expansion/rewrite — one stage, two
  names.
- **Latency regression accepted → D-DRAFT-16.** Rules are effectively free;
  Kev-4B is ~45 ms on an H100 and will be slower on an A4000, so 100-200 ms is
  the working expectation. Accepted because the objective is capability and
  because the cost is proportionally small — the live path already contains an
  **LLM expansion call** and an **LLM synthesis call** plus BM25 and
  reranking, so total query latency is in seconds. Latency becomes a
  first-class per-arm metric; Kev-0.8B is the fallback if the measured figure
  materially exceeds expectation.
- **Noted for a successor strand, not scheduled:** the expansion stage is
  itself an LLM call whose sampling makes phrases vary run-to-run
  (`service.py:213`). A decision model taking over an "expand or not" boolean
  would be a net latency *gain*. This is the first identified case where the
  original token-cost motivation and the latency concern point the same way.
- **Finding: no decision record exists for the rules-over-LLM choice.**
  `DECISIONS.md` has nothing on query classification — every hit concerns
  *heading* classification in the parser. The only recorded reason anywhere is
  `service.py:1185`, which calls wiring an `LLMProvider` for
  `LLMQueryAnalyzer` a **follow-up** — i.e. a plumbing gap, not a latency or
  accuracy verdict.

### Flags

- **Ask the architect why classification is rule-based.** The operator's
  hypothesis — an LLM was tried, latency hurt, rules won, and Jev-class speed
  now justifies revisiting — fits the artifacts but is **unsupported by the
  repository**. The distinction matters: if latency was the reason, this
  strand is directly well-motivated; if it was only plumbing, then no model
  has ever been measured at this seam and the rules have never been compared
  against anything, making phase 1's baseline the first such comparison ever
  run. Either answer is useful; the absence of a record is the thing to fix.

## 2026-10-13 — Session close: three-valued analyzer knob; drafts consolidated 17 → 14

### Done this session

- **Configuration redesigned to a single three-valued knob → D-DRAFT-14.**
  `NORA_QUERY_ANALYZER` = `rules` | `llm` | `decision`, resolved
  CLI > env > config > default, defaulting to `rules`; the provider roster
  moves beneath the `decision` value. Replaces the earlier boolean
  flag-plus-roster design. The operator's proposal and a better fit: it maps
  onto three implementations that already exist, and the name matches the
  `NORA_*` convention.
- **The `llm` value brought into scope**, having initially been set aside. It
  resurrects written-but-unwired code, restores a measurement arm previously
  dismissed as synthetic, and enables a comparison that has never been run at
  this seam. Consequence: the knob is the mechanism that makes
  `LLMQueryAnalyzer` reachable, so the `SUMMARIZE`/`FACT` fix is re-escalated
  from hygiene to a hard prerequisite shipped in the same change.
- **Design doc to v0.9** — §3 rewritten for the knob, §2.1 defect re-escalated,
  §6 rebuilt with four named arms, the divergence-weighted confusion matrix,
  the intrinsic/extrinsic split, and the inspectability-trade rationale.
- **Coupling quantified.** `intent.query_type` is read at **12 sites** in
  `core/src/query/pipeline.py`, driving retrieved-chunk count, BM25 weighting,
  rerank enable, expansion enable, distance cutoff, grouping and the synthesis
  path; in the sira lane `resolve_cells(intent, ...)` additionally decides
  which corpus cells are searched at all. Confirms the operator's instinct that
  classification is strongly coupled, and supplies the justification for
  weighting the confusion matrix by downstream divergence rather than counting
  label mismatches.
- **Eval-material audit.** No classification eval exists. A4 / A8 / A11 are
  end-to-end pipeline runs, not classification runs. Repo-wide labelled
  query→type examples live only in unit tests and are skewed by history —
  FACT 15, SUMMARIZE 13, SINGLE_DOC 7, STANDARDS_COMPARISON 3, GENERAL 3,
  FEATURE_LEVEL 1, CROSS_DOC 1, and **zero** for CROSS_MNO_COMPARISON,
  RELEASE_DIFF and TRACEABILITY. The real eval sets live under `<env_dir>/eval/`
  and are labelled with expected req_ids and golden responses, not query types.
- **Two-stage labelled-set plan agreed.** A synthetic/public dev set built
  outside the office carries no proprietary content and can therefore be
  **committed** — giving CI regression coverage the real set never could.
  `PROJECT.md` already contains safe seed queries covering two of the three
  zero-coverage classes. A corp set drawn from golden-eval samples and
  `ask-history` recordings follows later and stays under `<env_dir>/eval/`.
  **The dev set is for plumbing; the corp set is for verdicts** — synthetic
  queries are cleaner and differently distributed than real traffic, so dev-set
  scores must not be quoted as evidence the approach works. Hygiene for the
  committed set: no real plan codes or req IDs (two prior `git filter-repo`
  scrubs, 2026-05-09 and 2026-05-15).
- **SIRA fixtures: do not commit a cell DB.** `.gitignore:12` already ignores
  `*.db`, so it would need `git add -f`. Cell DBs carry full corpus text plus
  enrichment — the proprietary content the project rule bars — and a public
  mirror exists at `github.com/kurnoolion/nora`. The better path already exists:
  `sandbox/sira_query/test_cell_db_loader.py:40` defines `make_flat_cells()`,
  which should be promoted into a fixture generator — reproducible, committable,
  CI-usable. Real data moves out-of-band into `<env_dir>`, never through git.
  Also noted: phase 1 needs no SIRA at all, since `MockQueryAnalyzer.analyze()`
  is pure Python with no corpus dependency.
- **Drafts consolidated 17 → 14** at close. Mid-session correction chains were
  folded into the decisions they corrected rather than appended after them:
  the latent-defect correction into the defect-ownership draft, the live-lane
  placement re-verification into the placement draft, and Kev's clearance plus
  as-is selection into the Kev candidate draft. Substance preserved; the
  "I was wrong earlier" scaffolding removed, with the process lessons kept here
  in the journal where they belong.

### Process lessons from this session

- **Verifying what a thing contains is not verifying whether anything uses it.**
  Blast radius was asserted twice from map contents without checking call
  sites; both claims needed retraction. Check the callers.
- **Argue from the live lane.** Placement was first argued from
  `core/src/query/pipeline.py`, which is the `nora` lane; the live `sira` lane
  had to be checked separately. It agreed, but the method was wrong.
- **Capture cadence.** Drafting after every turn produced correction chains a
  future reader would have to follow. The project's own ritual — converse,
  then triage at close-session — exists for this reason; front-loading capture
  to avoid empty draft files over-corrected in the other direction.

### Next

- `superpowers:writing-plans` for the phase-1 implementation plan: defect fix
  shipped with the knob, synthetic labelled dev set, rules and llm baselines.
- Architect review of `decision-model-design.md` v0.9, especially §8.

### Flags

- **Five open points carried in design §8** awaiting the architect: module
  placement (`core/src/decision/` vs extending `core/src/llm/`); openjev's
  licence conflict (recommendation: drop it, since it is already parked on
  VRAM); fine-tuning budget — **the one to answer first**, since
  without SFT iteration 1 measures a model that has never seen a telecom
  question; production image/stack name; and phase-5 default-flip criteria.
- **Ask the architect why classification is rule-based.** No decision record
  exists. If latency drove it, this strand is directly well-motivated; if it
  was only plumbing, then no model has ever been measured at this seam and
  phase 1's baseline is the first such comparison ever run.
- **Governance: proprietary content in model weights is unruled.** Blocks any
  future CPT work (D-DRAFT-12). Distinct from the egress question in
  D-DRAFT-1.
- **Premise caveat for the architect.** This seam has no LLM call to displace,
  so iteration 1 saves no tokens. If cost reduction rather than capability is
  the driver, target ordering should be revisited — the token-spending
  decision points are reranking, SIRA chunk selection and the Stage-2 judge.
- Docs nit for a scaffold pass: `PROJECT.md`'s Contributors table still says
  `<doc_root>/corrections/`, stale since the D-022 rename to `<env_dir>`.
- "Procurement spec" from a dictated proposal remains unconfirmed — NORA's
  corpus is device requirement specifications.

## 2026-10-14 — Correction: the OOD gap is the label set, not the domain

### Done this session

- **Published a briefing artifact for the architect** covering what a decision
  model is, why hosted Jev is unusable, the NORA use case, requirements,
  options, alternatives, risks and roadmap. Private link; no proprietary corpus
  content.
- **Correction raised by the operator and applied** (design §4.2 → v1.0,
  D-DRAFT-10 amended). The earlier framing — "generic Kev will be weak on
  telecom text, so a fine-tune is the experiment" — conflated two different
  things and would have alarmed a reader into expecting telecom training:
  - **The classifier never reads spec prose.** It reads plain English
    questions, so telecom vocabulary is irrelevant to it. Domain training
    remains rejected (D-DRAFT-12) and nothing here revisits that.
  - **What Kev has not seen is NORA's label set.** It was trained to sort
    banking intents, news topics and film sentiment — never to place a
    question among ten `QueryType` values. `RELEASE_DIFF` and `TRACEABILITY`
    are NORA taxonomy, not English concepts, and some boundaries are pure
    convention: `analyzer.py` documents that "explain the X authentication
    requirements" routes to `SUMMARIZE` rather than `SINGLE_DOC` *even though
    it names a plan*. No language understanding produces that.
  - **Kev classifies zero-shot**, which was not previously recorded and is
    material. `/v1/systemone` takes the option set at inference time and the
    pointer head scores each option against the question, so **no training is
    a prerequisite to running it**. Iteration 1 measures zero-shot first.
  - **The remedy, if needed, is an ordinary task SFT** — a few hundred
    labelled questions teaching boundaries, drawn from the set phase 1 builds
    for evaluation regardless. One artefact, two uses; nothing extra to
    collect. Reached for only if the confusion matrix shows errors clustering
    on boundaries.
  - **The A11 parallel was overstated.** That cross-encoder scored
    query-against-chunk pairs over spec prose and went out-of-distribution on
    the *domain*; this classifier sees no prose. A11 stays a caution about
    generic models on NORA tasks, but Kev's own 0.790 is the figure to expect.
- **Consequence for §8 open point on fine-tuning budget:** it softens from
  "is the fine-tune in scope" to "if zero-shot falls short, is a task
  fine-tune in scope" — a smaller and more accurate ask.

### Flags

- **Watch this framing in any future summary.** "Fine-tune" in this strand
  means *task* SFT on labelled questions, never domain training on specs. The
  two were conflated once and the distinction matters both technically and in
  how the work is perceived.
- **Brief committed to the strand** as `decision-model-brief.html` (the page
  published at Version 2). Pre-commit scan clean: no MNO names, no req IDs, no
  plan-code shapes, no internal endpoint addresses; the only NORA-specific
  figures are the A11 results, already open in committed `STATUS.md`. Safe
  against the public mirror. Kept beside `decision-model-design.md` per the
  in-strand convention — the design doc is the working artefact, the brief is
  the shareable summary of it, and both must be updated together when the
  design changes.

## 2026-10-19 — Synced with main; references re-anchored

### Done this session

- **Merged `origin/main` (`fcc9fce`, 2026-10-18)** into the strand branch. No
  conflicts — the branch is docs-only and touches no file main moved. Local
  `main` fast-forwarded too. Main brought the `/drift-check dev-full` six code
  fixes, `/drift-check design` with canonical **D-254**, regenerated
  `MODULE.md` + `MAP.md`, and the `NORA_LLM_CONFIG` / `llm.json` doc and config
  corrections.
- **Every design claim re-verified against `origin/main` — all hold.**
  `QueryType` still carries the same ten members; `_TYPE_MAX_DISTANCE` is still
  exactly `{FACT: 0.4, SUMMARIZE: 0.7}`; `_TYPE_DISABLE_GROUPING` and
  `_TYPE_REWRITE_ENABLED` are intact with expansion still gated on query type;
  `pipeline.py` still defaults to `MockQueryAnalyzer`; the sira service still
  calls `make_query_analyzer(None)` with its "wiring an `LLMProvider` is a
  follow-up" comment; `make_query_analyzer` is still the same two-branch
  factory. Classification remains 100% rule-based.
- **All line references re-anchored** across the design doc, the drafts and
  the brief — every seam file moved on main, by up to ±20 lines. The design
  header now stamps the as-of commit (`fcc9fce`) and states plainly that
  identifiers are stable while line numbers are not. Design to v1.1.
- **Coupling count corrected: 13 read sites, not 12.** `intent.query_type` is
  read at 13 places in `pipeline.py` (431, 480, 509, 520, 521, 527, 532, 533,
  534, 583, 621, 639, 682). The original "12" was an undercount on my part,
  not a change introduced by the merge — it was 13 before as well. Fixed in
  the design, the drafts and the brief.
- **New analyzer public surface noted:** `query/MODULE.md` now lists
  `mno_surface_forms(mno)` — every written form of an MNO the analyzer
  recognises, longest first, read by the golden eval's text check to ignore
  operator-name-only edits. Relevant because it is new public surface in the
  module this strand will modify.

### Flags

- **The `SUMMARIZE`/`FACT` prompt defect survived a `/drift-check dev-full`.**
  It is still present at `analyzer.py:391` — the prompt still enumerates eight
  of ten `query_type` options. The audit made six code fixes and did not
  surface it, because `drift-check` compares MODULE.md *contracts* against
  code, not prompt *contents* against the enum they must agree with. **This is
  a blind spot in the audit worth raising with the architect independently of
  this strand**: any prompt that hard-codes a value set which also exists as an
  enum can drift silently and no current check will catch it. Candidate
  remedy, cheap: a test asserting the prompt's option list equals
  `[t.value for t in QueryType]`. That test is the real fix; the one-line
  prompt edit is only today's symptom.
- Line references will rot again on the next merge. The as-of stamp in the
  design header is the mitigation, not a cure — re-anchor after each sync.
