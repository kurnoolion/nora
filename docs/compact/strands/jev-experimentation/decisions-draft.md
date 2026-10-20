<!-- Consolidated at close-session 2026-10-13. Mid-session correction chains
     were folded into the decisions they corrected; the process lessons behind
     them live in journal.md. 17 working drafts -> 14. -->

### D-DRAFT-1 — Hosted Jev rejected; target is an open-weights Jev-class model served in-corp

**Date:** 2026-10-13
**Context:** The directive asks for a "proper Jev flow" replacing NORA's
LLM-based decision points. TypeSafe AI's Jev is the reference product for the
"System One" decision-model category — typed `bool` / `score` / `choice`
outputs with calibrated probabilities, single forward pass, 70-500 ms,
$0.042/M input tokens with output free.
**Decision:** The hosted Jev API is disqualified. Pursue the *category* via an
open-weights Jev-class model served inside the corporate network.
**Why:** Jev is closed-weights and hosted-only — no VPC, no on-premise, no
self-hosting. US West Coast with no region choice; retention "as long as
reasonably necessary" (zero-retention only via enterprise sales); SOC 2 Type II
third-party reported but unconfirmed in TypeSafe's own documents; no ISO 27001,
no BAA. NORA's corpus is proprietary MNO requirement content, `CLAUDE.md` bars
that content from logs and reports, and the build/serve environment permits
**no external API calls at all** (confirmed with the strand owner). There is no
configuration in which hosted Jev runs against this corpus.
**Consequences:** No TypeSafe contract or budget line. Every candidate must be
self-hostable on internal hardware. "Jev" in later artifacts means Jev-*class*,
not the product; no plan in this strand may assume access to the hosted vendor
service.

### D-DRAFT-2 — First target is query classification, not reranking

**Date:** 2026-10-13
**Context:** Seven LLM-or-rule-backed decision points exist in NORA (query
intent classification, rerank scoring, query rewriting, taxonomy feature
mapping, SIRA chunk selection, query expansion, golden-eval Stage-2 judge).
The experiment needs one.
**Decision:** The query analyzer (`core/src/query/analyzer.py`) is the first
and only target for iteration 1.
**Why:** It emits a `choice` — the category's native shape. It sees only the
user's query, never corpus text, so it is testable under the narrowest data
permission. `make_query_analyzer()` is already a factory, so swapping an
implementation touches one function. Decisively, a rule-based implementation
(`MockQueryAnalyzer`) already exists behind the same `analyze()` interface,
giving a multi-arm comparison with no new abstraction.
Rejected: **reranking** (`OpenAIRerankChat`, `reranker.py:519`), despite being
mechanically the most decision-model-shaped code in the repo — it already asks
an LLM for an integer score per doc. It reads chunk bodies, and it sits inside
the `nora-retrieval-quality` problem, cold for ten weeks with both NORA lanes
excluded from team-eval, so any effect would be unattributable against existing
retrieval starvation.
Rejected for now: **taxonomy feature mapping**, the bigger prize (STATUS flag
2026-05-03 records a 20pp eval swing traced to its non-determinism), because it
is a build-time stage with graph-topology consequences — too heavy for a first
comparison.
**Consequences:** The other six decision points are out of scope until this one
reports. Each needs its own egress assessment, since four read corpus content.
Note the premise shift recorded in D-DRAFT-11: this seam has **no LLM call to
displace**, so iteration 1 saves no tokens. The reasons above still hold, but if
token cost rather than capability is the driver, target ordering should be
revisited with the architect.

### D-DRAFT-3 — GGUF is disqualified for decision models; serving is per-candidate

**Date:** 2026-10-13
**Context:** NORA serves local models through Ollama, which is GGUF-based. The
obvious integration path is to pull a decision model and point the analyzer at
it.
**Decision:** GGUF distributions of decision models must not be used. Serving
is per-candidate: **Kev runs its own server** (`python -m kev.serve --run
jaredpalmer/kev-4b --port PORT`, exposing `/v1/systemone`); vLLM is the route
for the larger AutoTrust candidates if they are ever stood up. Ollama continues
to serve the legacy LLM path untouched.
**Why:** Published GGUF builds of these models **contain no decision component
at all**. AutoTrust's recipe keeps the backbone frozen and adds a small
*detachable* decision block — for JEV-9B, 40.2M trained parameters, ~0.5% of
backbone — which ships as separate files that GGUF conversion drops. What
remains is a plain Qwen or Gemma that loads, answers, and is silently the wrong
thing. Confirms the architect's suspicion that the current runtime does not
support a decision model. One known exception exists: Atlas3D's JEV-27B-VL
ships a `llama.cpp-jev.patch`, because the `jev` decision type is absent from
upstream llama.cpp.
**Consequences:** Candidate verification must assert the decision block is
actually loaded before any accuracy number is recorded. Concrete check, per
AutoTrust's own guidance: the file list must contain `head.safetensors`, an
adapter folder, and `calibration.json`; **a hollow distribution contains only
`.gguf` files**. What a stripped build omits is the decision adapter, the
24-slot decision head, and the calibration temperatures. Asserted in code, not
by inspection. Note the tension: the small models that fit the 16 GB GPU host are
precisely those distributed for Ollama / llama.cpp, so "runs on a laptop" in
any secondary source is unverified for decision capability until checked.

### D-DRAFT-4 — Hardware disposition: the 16 GB GPU host is the practical host; large candidates parked

**Date:** 2026-10-13
**Context:** Available internal hardware: a large-memory host, currently committed as
the backup host for LLM synthesis and therefore contended and not plannable
near-term (explicitly not ruled out), and an on-prem GPU host with 16 GB VRAM.
**Decision:** The 16 GB GPU host is the iteration-1 target. Candidates requiring more
are parked, re-entering if the synthesis backup host frees up.
**Why:** Verified VRAM requirements. The AutoTrust family — JEV-9B
(Qwen3.5-9B, 24 GB min), JEV-27B (Qwen3.8-27B, 80 GB+), JEV-27B-VL (80 GB+
full, Q4_K_M ~24 GB), GEV-26B-Decide (Gemma 4 26B-A4B MoE, 80 GB+ full, NVFP4
17.1 GiB needing 24 GB) — **fits none of it in 16 GB**, and is parked on VRAM
alone, never on licence (all Apache 2.0). openjev (DiffusionGemma 26B-A4B,
native `/v1/systemone` plus `/v1/decisions`, 698 stars, 30 ms p50 on an RTX
PRO 6000, confidence `1 - H(p)/ln K`) needs 24 GB+ on its vLLM backend; its
MLX backend wants ~16 GB on Apple silicon, which NORA does not deploy.
**One route back exists and should be recorded:** AWQ via `llm-compressor`
inverts the GGUF trap — the precedent `aioutfitters/JEV-27B-VL-AWQ` quantizes
the backbone to 4-bit (W4A16_ASYM, group 128) while **keeping the decision
head in bf16**. Applied to JEV-9B (18 GB bf16), 4-bit weights land near 5 GB
and should fit the 16 GB GPU host. Its published figures are the strongest of any
candidate: mean KL ~0.019 to hosted Jev, **90% of Jev's accuracy at 16
options** (exactly `_FEATURE_KEYWORDS`' cardinality), 2.6x faster than
JEV-27B. Not pursued in iteration 1 because no JEV-9B-AWQ is published — NORA
would be first, which could cost a day or a week, and unproven conversion work
inside a first exploration risks failing for reasons unrelated to decision
models.
**Consequences:** Iteration 1 carries no hardware dependency beyond what is
already in place. The general rule for whoever picks this up: **small models
fit the 16 GB GPU host as-is; large models fit the synthesis backup host as-is; anything between needs
shrinking, and shrinking must use AWQ, not GGUF.** Caveat on the backup host: its
unified memory will *fit* the large models but is slower than dedicated GPU
memory, so "fits" is not "fast" until measured. Its exact specification remains
an open architect question.

### D-DRAFT-5 — Baselines are measured for accountability; they do not gate the exploration

**Date:** 2026-10-13
**Context:** Published counter-evidence argues small purpose-trained
classifiers often match or beat decision models on trained tasks, and that the
calibration advantage is overstated. An earlier framing treated that as grounds
for the exploration to terminate at "use the classifier instead".
**Decision:** The rule-based and small-classifier arms are measured as
**reference baselines**, reported honestly, and do **not** gate the
exploration. The strand proceeds regardless of whether a baseline wins.
**Why:** The objective is capability, not a leaderboard win (D-DRAFT-6) — a
baseline that beats the decision model is a result to *record*, not a reason to
stop. Baselines remain mandatory: without them the experiment yields an
unanchored number, which is the difference between exploration and decoration.
The counter-evidence: a 200M DeBERTa scored 89.01% at 54 ms against hosted
Jev's 86.35% at 348 ms on prompt-injection detection; a 125M model hit 80.27%
at 33 ms on content safety; hosted Jev runs overconfident at 0.81 mean
confidence against 0.74 actual accuracy (ECE 0.074); and JEV-9B's published ECE
of 0.0007 is computed against the teacher's distributions — closeness to Jev,
not accuracy against truth (0.0759 independently).
**Consequences:** Phase 1 carries a labelling step the directive does not
budget. Success criteria must be capability plus accountable measurement, not
"beat the rules", or a null result reads as failure when it is not. Reporting
must separate three questions that will otherwise be conflated: does the
decision path **work**, is it **faster**, is it **more accurate**. Legitimate
answers: yes / no / no. Note the resolution in D-DRAFT-10: because Kev is
LoRA-trainable, a Kev fine-tuned on NORA's labels *is* the small
purpose-trained classifier the counter-evidence favours, so the two arms merge
rather than compete.

### D-DRAFT-6 — The objective is capability-building under configurability, not a first-shot win

**Date:** 2026-10-13
**Context:** Decision models are an emerging category with a real speed
advantage and growing adoption. The architect-side decision is to build
in-house expertise now rather than wait for the category to settle, using
NORA's query classification as the vehicle.
**Decision:** This strand ships a configurable decision path in NORA and
explores its effectiveness. The deliverable is working, configurable
integration plus recorded expertise — not a proven accuracy improvement. The
work proceeds irrespective of baseline outcomes.
**Why:** Deliberate capability investment. The category is fast and gaining
adoption; the expertise transfers to NORA's six other decision points and to
classification work beyond NORA. Waiting for external consensus forfeits the
learning window, and integration cost is bounded because the seam is one
factory function. An accuracy win is a bonus, not the success condition. A
secondary and genuine motivation: rule-based classification is not scalable as
the class set and phrasing diversity grow, and is brittle to any phrasing
nobody anticipated.
**Consequences:** Legacy remains the configurable default throughout, so the
exploration never risks production behaviour. Success criteria: the decision
path runs configurably, candidate swapping works, and per-class accuracy,
determinism and latency are recorded against baselines. **"The baseline won" is
a successful outcome of this strand, not a failed one.** Negative findings are
first-class deliverables — the GGUF trap (D-DRAFT-3) is already one.

### D-DRAFT-7 — The decision model owns only the choice- and bool-shaped fields of QueryIntent

**Date:** 2026-10-13
**Context:** `analyze()` returns a whole `QueryIntent`
(`core/src/query/schema.py:62`) with nine populated fields, not a single label.
A decision model emits only `bool` / `score` / `choice`, so it cannot be a
drop-in replacement for the method.
**Decision:** Partition `QueryIntent` by output shape. The decision model owns
`query_type` (a 10-way `choice`) and `doc_type_scope` (a 3-way `choice`), and
optionally `mnos`, `likely_features` and `plan_ids` as **parallel booleans, one
question per closed-vocabulary candidate**. The open-extraction fields —
`entities`, `concepts`, `releases`, `standards_refs` — stay on the existing
regex paths in `MockQueryAnalyzer`. Phase 1 scope is the two `choice` fields
only; the multi-selects are deferred to phase 3 so the first measurement has
one variable.
**Why:** The partition follows the data. `query_type` and `doc_type_scope` are
closed option sets — native `choice` shape. `mnos` (3), `likely_features` (16
keys) and `plan_ids` (5) are closed-vocabulary multi-selects decomposing into
per-candidate parallel questions, which is the one-state-many-questions pattern
the category is built for: 16 + 3 + 5 = 24 booleans plus 2 choices = 26
questions against one state, comfortably inside documented concurrency and a
64k/32k budget. The remaining four fields are free-text span extraction with no
option set, so no decision model can produce them; `MockQueryAnalyzer` already
implements every one of those extractors in tested regex.
**Consequences:** The integration is a **hybrid** analyzer, not a swap — a
decision-model classifier composed with existing rule-based extractors. This is
cleaner than `LLMQueryAnalyzer`, which asks one LLM call to do classification
and extraction together and falls back wholesale to `MockQueryAnalyzer` on any
JSON parse failure.

### D-DRAFT-8 — Classification runs before query expansion; the per-type gate forces it

**Date:** 2026-10-13
**Context:** The directive asks explicitly whether the decision step should run
before or after query expansion (called "query enrichment" in the SIRA
service — one stage, two names).
**Decision:** Before. Classification stays the first stage; expansion remains
downstream. Current behaviour is preserved.
**Why:** Not a preference — the code forecloses the alternative, and this was
verified in **both** lanes rather than inferred from one. In the `nora` lane,
`core/src/query/pipeline.py:480` gates expansion on
`_TYPE_REWRITE_ENABLED.get(intent.query_type, False)`, with `analyze()` at
`:401` and `rewrite()` at `:501`. In the live `sira` lane, `analyze()` is step
1 of `_multi_cell_query` (`sandbox/sira_query/service.py:1199`) and expansion
happens later inside retrieval (`filter_query_expansion` /
`search_with_expansion`, `:1018`-`:1036`). Expansion is a *consequence* of
classification, so placing the decision model after it would leave nothing to
decide whether to expand.
**Consequences:** The decision model sits at the earliest point of the
interactive path, so its latency is in series with everything downstream
(D-DRAFT-13). More importantly, **classification is strongly coupled to the
whole pipeline**: `intent.query_type` is read at 13 sites in `pipeline.py`,
driving retrieved-chunk count (`resolve_top_k`, `:509`), BM25 weighting
(`resolve_bm25_weight`, `:520`), rerank enable (`:521`), expansion enable
(`:499`, `:532`), distance cutoff (`:533`, `:583`), grouping (`:534`, `:621`)
and the synthesis path (`:431`, `:682`); and in the sira lane
`resolve_cells(intent, ...)` decides which corpus cells are searched at all.
A misclassification therefore propagates into every downstream knob, which is
why the measurement requires a **divergence-weighted confusion matrix** over
all 10 classes rather than an overall accuracy figure. Worked example: a
candidate at 90% overall that confuses `FACT` with `SUMMARIZE` inverts two
opposed configurations — tight top_k + strict 0.4 threshold + rerank on, versus
wide top_k + 0.7 threshold + grouping off — and could be worse in practice than
the rule-based baseline.

### D-DRAFT-9 — `/v1/systemone` is NORA's internal decision-provider interface

**Date:** 2026-10-13
**Context:** The directive requires a selectable roster of decision models
mirroring the existing LLM roster. Several open implementations expose
TypeSafe's own `/v1/systemone` HTTP API (Kev, Rizzo Flow, openjev).
**Decision:** Define a `DecisionProvider` protocol speaking `/v1/systemone` —
typed `bool` / `score` / `choice` questions against one shared `state` — with
concrete providers behind it and a roster selecting among them.
**Why:** Mirrors a pattern this codebase has validated. `OpenAICompatibleProvider`
speaks `/v1/chat/completions` over stdlib urllib with no SDK dependency, which
made the LLM roster vendor-neutral and let `llm-model-discovery` add model
selection without touching providers. Adopting the de-facto decision-model wire
format buys the same property: swapping candidates is a config change, and if
hosted Jev ever became reachable it is the same interface with no rework.
Protocol-based abstraction is the stated project convention (`LLMProvider`,
`EmbeddingProvider`, `VectorStoreProvider`).
**Consequences:** Candidates without a `/v1/systemone` endpoint
(ModernBERT-class; constrained decoding over a resident model) need a thin
in-process adapter rather than an HTTP hop — acceptable, and it keeps the
roster honest about what each arm is. Module placement is still open: a new
`core/src/decision/` package (proposed, 17th package) versus extending
`core/src/llm/`. Either way it needs its own `MODULE.md`, shipped in this
branch per the CLAUDE.md branch-flow rule.

### D-DRAFT-10 — Kev-4B leads iteration 1, unmodified; provenance and licence cleared

**Date:** 2026-10-13
**Context:** The iteration-1 candidate had to be a native `/v1/systemone`
implementation fitting 16 GB, with provenance and licence verified before any
code or weights enter the corporate network.
**Decision:** **Kev-4B on the 16 GB GPU host, unmodified**, leads iteration 1. No
quantization, no dependency on the synthesis backup host. Canonical upstream is
**`jaredpalmer/kev`** — the original, no fork marker, 9.1k stars, 343 commits;
the other `kev` repositories are forks. **Apache-2.0 covers the repository, the
LoRA adapter and the pointer head**, with Qwen3 / Qwen3.5 / Qwen3.8 bases also
Apache-2.0.
**Why:** Kev checkpoints are a rank-16 LoRA adapter plus a small bilinear
pointer head on a Qwen base; the readout scores each option's hidden state
against the question's `<decide>` hidden state and softmaxes — not a letter-row
argmax. Kev 1.0 shipped 0.8B / 4B / 9B (Qwen3.5) and 27B (Qwen3.8); weights at
`huggingface.co/collections/jaredpalmer/kev`. It runs out of the box on
hardware and a runtime already in place, so iteration 1 spends effort on the
seam, the labelled set and the measurement rather than model engineering.
**Kev's trainability is the decisive property** — it resolves D-DRAFT-5's
tension, since a Kev fine-tuned on NORA's labelled queries *is* the small
purpose-trained classifier the counter-evidence favours, with calibrated typed
outputs and the decision interface.
**Consequences:**
- **bf16 is mandatory**, not optional: the card states "4B fp32 needs ~16 GB",
  which saturates the 16 GB GPU host leaving nothing for context. `KEV_DTYPE=bf16`
  roughly halves it to ~8 GB.
- **Generic Kev-4B will be weak and badly calibrated on this corpus, by its own
  documentation.** In-distribution accuracy 0.854, **out-of-domain 0.790**, OOD
  Brier 0.328, **ECE 0.096, explicitly described as uncalibrated out of
  domain**; locked test 0.856 / 0.806. Latency ~45 ms on an H100, so slower on
  the 16 GB GPU host. Its training suite is ~13,576 generic text-classification records
  over two epochs (banking77, BoolQ, AG News, MultiNLI, SST5, Yelp, TREC,
  DBpedia, Amazon Reviews, IMDB) with nothing resembling telecom requirement
  specifications, and the card states that "product-shaped questions with no
  training analogue are not guaranteed" — exactly NORA's task.
  **Amended 2026-10-14:** the gap is the **unfamiliar label set**, not the
  telecom domain. This classifier reads English questions and never touches
  spec prose, so telecom vocabulary is irrelevant; what Kev has not seen is
  NORA's ten `QueryType` values and the conventions separating them. Kev also
  classifies **zero-shot** — `/v1/systemone` takes the option set at inference
  time — so no training is a prerequisite to running it. The remedy, if
  needed, is an ordinary task SFT on a few hundred labelled questions reusing
  phase 1's eval set, reached for only if the confusion matrix shows boundary
  clustering. See design §4.2 v1.0.
  This independently confirms the out-of-distribution warning already derived
  from NORA's own A11 cross-encoder result (`ms-marco-MiniLM-L6-v2`, net-zero
  and slightly regressive at 86.4% / 62.3%, diagnosed as OOD on telecom text).
  **Out-of-the-box calibration — the category's headline selling point — is
  therefore not available for this corpus, and the LoRA fine-tune is not an
  enhancement but the experiment itself.** Scale is encouraging: 13.5k records
  over two epochs is a small job.
- Accuracy expectation set up front: iteration 1's ceiling is whatever a
  generic 4B decider achieves on telecom text. Accepted, not discovered later.
- Minor item for pull time: the `kev-4b-qwen3` card cites `Qwen/Qwen3-4B-Base`
  while the repo overview says Qwen3.5-4B-Base — confirm which variant.

### D-DRAFT-11 — This strand owns the `SUMMARIZE`/`FACT` prompt-omission fix

**Date:** 2026-10-13
**Context:** `QueryType` carries ten members. `LLMQueryAnalyzer`'s prompt
(`analyzer.py:391`) enumerates eight and omits `summarize` and `fact`, so
neither class can be emitted on that path. `MockQueryAnalyzer` emits both
(`:279`, `:293`). The per-type maps make this consequential:
`_TYPE_MAX_DISTANCE` (`:105`) has exactly two keys, `FACT: 0.4` and
`SUMMARIZE: 0.7`; `_TYPE_DISABLE_GROUPING` (`:180`) has exactly one,
`SUMMARIZE`. `_TYPE_REWRITE_ENABLED` (`:132`) loses only its `SUMMARIZE` entry.
**Decision:** This strand fixes the defect, shipped in the same change that
introduces the analyzer knob (D-DRAFT-14). Not routed elsewhere for
adjudication.
**Why:** It sits in this strand's target seam, it was found by this strand's
characterization work, and it is a prompt-string edit plus a regression test —
routing it elsewhere would stall phase 1 behind another queue.
**Scope correction, important:** the defect is **latent, not live**.
`LLMQueryAnalyzer` is **never constructed in any live path** —
`core/src/query/pipeline.py:271` is `self._analyzer = analyzer or
MockQueryAnalyzer()`, and `sandbox/sira_query/service.py:1199` is
`make_query_analyzer(None)` with a comment recording that wiring an
`LLMProvider` for it is a deliberate follow-up. Outside tests nothing
instantiates it. **Query classification in the serving path is 100% rule-based
today**, both omitted classes are reachable, and the tuned 0.4 / 0.7 thresholds
do take effect. An earlier claim that NORA's A4 / A8 / A11 baselines ran with
the defect live is **withdrawn** — those ran on the rule-based analyzer and are
sound.
**Consequences:** The fix does not gate baseline measurement. It **is** a
prerequisite for the `llm` value of D-DRAFT-14's knob, which is the mechanism
that makes `LLMQueryAnalyzer` reachable; without it that mode would be wrong by
construction, silently unable to emit two of ten classes with two per-type
features inert. A typed option set is a schema rather than prose in a prompt,
so the failure mode cannot recur on the decision path.

### D-DRAFT-12 — No domain-adaptive pre-training for query classification; SFT only

**Date:** 2026-10-13
**Context:** Kev-4B's card documents weak, uncalibrated out-of-domain behaviour
on generic benchmark training data. One proposed remedy was CPT — "seasoning"
the base on NORA's telecom requirement specs before task fine-tuning.
**Decision:** CPT is **out of scope** for query classification. Adaptation is
**SFT on labelled queries only**.
**Why:** The classifier's input is **user questions**, not corpus text. What
separates `RELEASE_DIFF` from `TRACEABILITY` is **question shape**, not telecom
vocabulary. CPT teaches the language of *documents*, a different distribution
from the language of *questions about* documents, and could degrade the task by
pulling the representation toward prose never seen at inference. Short English
questions over ten stable classes is a favourable shape for SFT, so the simpler
intervention is also the better-aimed one. Two further costs made the rejection
easy: Kev **freezes its backbone by design**, so CPT invalidates the adapter and
head and means rebuilding the decision block against a NORA-specific base; and
CPT would **bake proprietary spec content into model weights**, a governance
category nobody has ruled on.
**Consequences:** Adaptation work is bounded to a LoRA SFT run on the labelled
query set — a day's work, which is what makes including it in this strand
reasonable. **Not a general rejection:** CPT is the natural remedy for the
decision points that *do* read chunk bodies — reranking, SIRA chunk selection,
the Stage-2 judge — and belongs to a successor strand, after the
weights-governance question is ruled on.

### D-DRAFT-13 — Latency regression at the classification seam is accepted, and must be measured

**Date:** 2026-10-13
**Context:** Replacing rule-based classification with a decision model trades
effectively-free string matching for a model forward pass. Kev-4B is ~45 ms on
an H100; the 16 GB GPU host will be slower, so 100-200 ms is the working expectation.
**Decision:** The regression is **accepted**. Per-arm latency is recorded as
required output alongside accuracy.
**Why:** The objective is capability and better classification, not preserving
a zero-cost stage (D-DRAFT-6), and rules do not scale with class count or
phrasing diversity. The cost is also proportionally small: the live query path
already contains an **LLM expansion call** and an **LLM synthesis call**, plus
BM25 and reranking, so total query latency is in seconds and 100-200 ms at the
head is a few percent. A budget must still be stated, because "a few percent"
is an estimate.
**Consequences:** Latency is a first-class reported metric per arm. If the
measured figure materially exceeds expectation, Kev-0.8B is the fallback before
abandoning the approach. Noted for a successor strand, not scheduled: the
**expansion stage is itself an LLM call** whose sampling makes phrases vary
run-to-run (`sandbox/sira_query/service.py:221`) — the same nondeterminism that
forced expansion OFF for `corp-db-layer`'s identity gate. A decision model
taking over an "expand or not" boolean would be a net latency *gain*, a token
saving, and a determinism win simultaneously — the first identified case where
all three motivations align.

### D-DRAFT-14 — A single three-valued `NORA_QUERY_ANALYZER` knob; the `llm` value is in scope

**Date:** 2026-10-13
**Context:** The design initially specified a boolean flag selecting
legacy-versus-decision, plus a roster beneath it. The strand owner proposed a
single knob selecting rule-based, decision-based or LLM-based, initially
describing the `llm` value as out of scope.
**Decision:** Configuration is **one three-valued knob**,
`NORA_QUERY_ANALYZER` = `rules` | `llm` | `decision`, resolved CLI > env var >
config > default, defaulting to `rules`. The provider roster moves *beneath*
the `decision` value. **The `llm` value is in scope.**
**Why:** It maps exactly onto implementations that already exist — `rules` →
`MockQueryAnalyzer` (live default), `llm` → `LLMQueryAnalyzer` (written, never
wired), `decision` → the new hybrid analyzer — and the name matches the
established convention (`NORA_LLM_PROVIDER`, `NORA_EMBEDDING_PROVIDER`,
`NORA_SIRA_INDEX`, `NORA_SIRA_ENGINE`). The `llm` value earns inclusion on
three counts: it resurrects written code that currently does nothing; it
restores a measurement arm otherwise dismissed as synthetic; and it permits a
comparison that **has never been run at this seam** — no decision record exists
for why classification is rule-based, and the only recorded reason
(`sandbox/sira_query/service.py:1193`) calls LLM wiring a *follow-up*, i.e. a
plumbing gap rather than a latency or accuracy verdict.
**Consequences:** The knob is what makes `LLMQueryAnalyzer` reachable, so
D-DRAFT-11's fix becomes a hard prerequisite shipped in the same change. Cost
is a few lines in the factory for the `nora` lane plus a thin adapter in the
`sira` lane, where the service holds an httpx LLM rather than an `LLMProvider`
object — small, not free. `make_query_analyzer()` (`analyzer.py:425`) currently
takes a single `llm_provider=None` argument and branches two ways, so this is a
signature extension; its docstring records the governing posture from
`multi-mno-sira` D-DRAFT-9 ("the same configured-LLM-or-mock posture as the
rest of NORA"), which the knob should mirror. Default `rules` preserves today's
behaviour byte-identically, which is what makes a phase-2 identity gate
achievable.

---

<!-- Drafts 15-21 added 2026-10-20 during the phase-1/2/3 implementation
     pass. Each records a choice where an alternative was seriously
     considered and rejected, per CLAUDE.md's branch-flow rule. -->

### D-DRAFT-15 — Confusion cost is derived from the live knob tables, not hand-authored

**Date:** 2026-10-20
**Context:** Design §6 requires the confusion matrix be "weighted by
downstream divergence" because each `QueryType` selects a different retrieval
configuration. It does not say how to obtain the weights.

**Alternative rejected:** a hand-authored cost matrix. With ten classes that
is ninety numbers, every one of them invented, and nothing would keep them
honest as the pipeline's tuning moved.

**Decision:** the cost of predicting B when the truth is A is the fraction of
the five per-type retrieval knobs whose values differ between A and B,
computed at import from `env.config._BROAD_QUERY_TYPES` /
`DEFAULT_BM25_WEIGHT_BY_TYPE` and `query.pipeline._TYPE_REWRITE_ENABLED` /
`_TYPE_MAX_DISTANCE` / `_TYPE_DISABLE_GROUPING`. Derived, never copied — a
copy would need an agreement test to stay honest, and deriving is both less
code and structurally correct.

**Evidence it is the right measure:** it reproduced both of §6's anchor claims
without being tuned to them. `FACT`↔`SUMMARIZE` scores 1.0 (all five knobs
inverted; §6 called it catastrophic). `CROSS_DOC`↔`FEATURE_LEVEL` scores 0.0
(identical configurations; §6 called it mild).

**Consequence:** the cost function moves when the pipeline's tuning moves, so
every report is stamped with `profile_digest()` and two reports are comparable
on weighted cost only when their digests agree. Same comparability-key pattern
`eval/golden.py` already uses.

### D-DRAFT-16 — Rerank is excluded from the knob profile

**Date:** 2026-10-20
**Context:** Six per-type knobs exist, not five. `_TYPE_RERANK_ENABLED`
(`pipeline.py:163`) is the sixth.

**Decision:** exclude it. The map is empty by design — the
`nora-retrieval-parent-displacement` strand deliberately removed its gating —
so every class resolves to the same `_DEFAULT_RERANK_ENABLED = True`. A
constant column contributes 0.0 to every pairwise distance and would only
dilute the five that carry signal.

**Guard:** a test asserts `_TYPE_RERANK_ENABLED == {}` and that `"rerank"` is
not in `KNOB_NAMES`. The day someone adds a per-type override, that test fails
and the profile is forced to grow a sixth knob rather than silently
mis-weighting.

### D-DRAFT-17 — `HybridQueryAnalyzer` composes the rule analyzer rather than reusing its extractors

**Date:** 2026-10-20
**Context:** Design §2 assigns `entities`, `concepts`, `releases` and
`standards_refs` to "the regex extractor", which lives in
`MockQueryAnalyzer._extract_*` — private methods in another module.

**Alternative rejected:** calling those private methods from
`core/src/decision/`. Reaching across a module boundary into privates couples
the two permanently and makes the rule analyzer's internals load-bearing
public surface by accident.

**Decision:** call `MockQueryAnalyzer.analyze()` once and replace the two
choice fields with `dataclasses.replace`. One call fills every
open-extraction field *and* yields a complete fallback `QueryIntent` for free.
The rule classifier then runs needlessly, which costs microseconds.

**Consequence:** `decision` imports `query` at module level, so `query` may
import `decision` only function-locally — the local import inside
`make_query_analyzer` is what breaks the cycle, and that is recorded in both
MODULE.md files rather than left for a reader to rediscover.

### D-DRAFT-18 — The knob is read by a separate factory, not inside `make_query_analyzer`

**Date:** 2026-10-20
**Context:** Design §3 says adding the decision path needs "a signature
extension (a decision-provider argument, or a config read inside the
factory)".

**Alternative rejected:** reading `NORA_QUERY_ANALYZER` inside
`make_query_analyzer`. Every existing caller passes a single positional
argument, so a config read there would make all of them knob-sensitive at
once and put the phase-2 identity gate at risk from a function nobody
reviewed for it.

**Decision:** `make_query_analyzer(llm_provider=None, decision_provider=None)`
stays pure argument dispatch, with both existing branches untouched — so every
current caller is byte-identical by construction. A second function,
`make_query_analyzer_from_knob`, reads the knob, and only the two call sites
that should be knob-aware adopt it.

**Also decided:** every arm **degrades to `rules` rather than raising** when
its provider is missing. The sira lane has no `LLMProvider` wired yet (its own
recorded follow-up) and the `systemone` provider needs a base URL that may not
be set. A misconfigured classifier must not take the query path down.

### D-DRAFT-19 — No `config/decision.json` yet

**Date:** 2026-10-20
**Context:** Design §3 says a provider roster sits beneath the `decision`
value. The LLM chain's precedent is four tiers — CLI > config DB > env var >
`config/llm.json` — plus a named multi-entry roster with a cached loader and a
test reset hook.

**Decision:** implement the single-provider `NORA_DECISION_*` chain only, two
tiers (CLI > env var > default). A file plus a cached loader plus a reset hook
for keys nobody sets is machinery ahead of demand, and
`resolve_standards_source` in the same module is two-tier for the same reason.
`core/src/llm/` grew the same way — single chain first, the roster added later
by `llm-model-discovery` when there was a second endpoint to choose between.

**Revisit when:** phase 3 has two real candidates to select between, which is
exactly when a roster earns its cost.

### D-DRAFT-20 — Two independent gates against silently measuring the wrong thing

**Date:** 2026-10-20
**Context:** Design §7 requires decision heads be asserted loaded before any
accuracy number is recorded, because a hollow build answers without erroring.
Implementing that surfaced a second route to the same failure.

**Decision:** two gates, because the degradation arrives two ways.

1. **Weights** — `assert_decision_head_present(model_dir)` requires
   `head.safetensors`, `calibration.json` and a decision adapter, and rejects
   a GGUF-only directory with the hollow-build diagnosis. `classify_cli`
   requires `--weights-dir` for any non-mock decision provider and exits 2.
   The mock is exempt: it measures nothing and claims nothing.
2. **Transport** — `HybridQueryAnalyzer.fallback_count`. Per-query fallback to
   the rules is the correct *serving* behaviour, but it makes a dead provider
   look like a successful run. `classify_cli` refuses any non-zero count under
   a `decision` label.

**Found the hard way, which is why gate 2 exists:** a run with a verified head
and an unreachable server completed, exited 0, and reported accuracy 0.720 /
weighted cost 0.136 — *exactly the rules baseline* — labelled `arm=decision`.
The head check had passed; nothing was watching the wire.

**Limitation to raise with the architect:** the weights check is a filesystem
check, so it runs where the weights are. For a remotely served model that is
the serving host, not the eval client. Either the check runs as a deploy-time
assertion on the serving host, or the server grows a route that reports its
loaded components. The current CLI chooses to refuse rather than to trust.

### D-DRAFT-21 — Phase 3's gate is NOT met; the code landed without the measurement

**Date:** 2026-10-20
**Context:** Phase 3's gate is "four-arm comparison complete". The work was
done unattended overnight on the user's instruction to proceed through all
phases.

**Decision:** land phase 3's *code* — the `/v1/systemone` provider, both
verification gates, the `decision` arm in the CLI, all tested against stubs —
and explicitly **not** claim the gate. Two arms of four are measured.

**Why the remaining two were not run, rather than left unexplained:**

- **Arm 3 (generic Kev-4B)** needs roughly 8 GB of third-party model weights
  fetched from Hugging Face. That is a large external download plus
  third-party code to run it, on a machine whose owner was asleep. `CLAUDE.md`
  requires confirmation before anything that leaves the machine, and a
  pre-pull was offered and not yet answered.
- **Arm 4 (fine-tuned Kev-4B)** is conditional on §8's fine-tuning-budget
  question, which is the architect's to answer.

**Also blocked, for the record:** phase 4 needs a trial stack on new ports and
a team-usage round; phase 5's default flip needs both a measurement that
supports it and §8 open point 5's criteria from the architect. Neither is
unattendable work that was skipped — both require people.

### D-DRAFT-22 — The server, not the weights directory, is the verification authority

**Date:** 2026-10-20
**Context:** D-DRAFT-20's limitation — "the weights check is a filesystem
check, so it runs where the weights are" — turned out to have an answer
already in the wire format. The reference server exposes `GET /v1/models`,
which reports the loaded `lora`, the calibration `temperature`, the `base`,
the `device`, the `backend` and the `dtype`.

**Decision:** make `assert_remote_head(base_url)` the gate for the decision
arm and demote `assert_decision_head_present(model_dir)` to an optional extra
check. `--weights-dir` is no longer required.

**Why, and why this is not a weakening:** the local check was the thing that
produced false assurance. It passed, the server was unreachable, and the run
reported the rules baseline under `arm=decision`. A check that runs on the
eval client can only ever describe the eval client. The remote probe runs over
the same connection the decisions will travel, so it fails on an unreachable
host — which is precisely the case that slipped through.

**Rejected alternative:** keep `--weights-dir` mandatory *and* add the probe.
Rejected because it keeps a requirement that cannot be satisfied in the
deployment this is being built for — the serving host is a different machine,
and the eval client has no weights to point at. A mandatory flag that cannot
be satisfied honestly invites a fake value.

**Generalisable:** ask the thing that will do the work what it is, rather than
inspecting an artefact you believe it will use.

### D-DRAFT-23 — The wire schema was guessed, and the guess was wrong in four places

**Date:** 2026-10-20
**Context:** `systemone_provider.py` was written with a docstring stating
plainly that the schema was **unverified against a live server**, and a parse
designed to fail loudly and name the keys the server actually sent. Setting
Kev up made verification possible against `kev/api.py` at jaredpalmer/kev
`1f04cfa`, the reference implementation of TypeSafe's public System One API.

**What was wrong:**

| Guessed | Actual | Consequence |
|---|---|---|
| `questions` is a list | an object keyed by question id | 422 |
| `question` | `instructions` | 422 |
| `options` (list) | `criteria` (dict for choice, list for score) | 422 |
| `answers` is a list | an object keyed by question id | mis-parse |
| answer holds `value` | holds `choice` / `noul` / `score` | mis-parse |
| a yes/no question is a `bool` | it is a **`noul`** | 422 |
| head ships `head.safetensors` + `calibration.json` | ships `head.pt`, temperature inside it | the gate **rejects the genuine article** |

**Decision:** correct all of it and keep the loud-failure parse, which did its
job — it would have named each mismatch rather than mis-mapping a field.

**The lesson is about the ones that were silent, not the ones that 422.** The
four request-shape errors announce themselves on first contact. The head-file
names do the opposite: a wrong `REQUIRED_HEAD_FILES` makes the gate reject
every real distribution, and the natural reaction to a gate that fires is to
assume the artefact is bad. A guess becomes an invariant the moment it is
written as a constant in a gate, and nothing in the code distinguishes the two.
`REQUIRED_HEAD_FILES` now carries a comment saying what it was and why it was
wrong.

**Also worth recording:** `pip install kev` installs an unrelated PyPI package
(it pulls jupyterlab, boto3, redis and ~90 others). The decision model is
installed from `github.com/jaredpalmer/kev`. The obvious command is the wrong
one, which is worth a line in any runbook.

### D-DRAFT-24 — Both found defects fixed, and one of them moved the baseline

**Date:** 2026-10-20
**Context:** Two defects were found during the phase-1 measurement and
deliberately left unfixed so that fixing them could not alter an arm
mid-measurement. The baseline is now recorded, so the hold is released.

**Decision:** fix both.

1. `LLMQueryAnalyzer` discarded a whole `QueryIntent` when any single typed
   field was unparseable. Now degrades per field, matching
   `HybridQueryAnalyzer._coerce`.
2. The cross-doc signal counted `_PLAN_ALIASES` hits rather than distinct plan
   ids, so one plan named through two of its own aliases routed a within-plan
   lookup to `CROSS_DOC`.

**Measured effect of (2):** the rules arm moves **0.720 → 0.760** accuracy and
**0.136 → 0.112** weighted cost on `classify-dev-v1`, from exactly two
samples, both wrong → right, nothing regressed:

- c02 `"Which AT commands must the device support?"` — `cross_doc` → `single_doc`
- c04 `"Band 13 network access requirements for {MNO_A}"` — `cross_doc` → `single_doc`

**Note on the test suite:** three of the eight new regression tests fail
against the unfixed code. The other five pass either way, because the
rule-based fallback happens to agree on those inputs. They are kept for
documenting intent; only three of them are coverage.

**Consequence for the strand:** the bar the decision model has to clear went
up. 0.760 is the number arm 3 is measured against, not 0.720.

### D-DRAFT-25 — `hybrid` retired as a name; confidence routing is its own arm

**Date:** 2026-10-20
**Context:** One word had come to mean two unrelated things in this strand.
`HybridQueryAnalyzer` meant *decision model for the two label fields, regex
for the spans, rule-based analyzer on provider error* — a composition, with no
reference to confidence. The headline table's `hybrid` row meant *keep the
model's answer where its calibrated confidence clears a floor* — a routing
policy, with no reference to the composition. A reader asked which of the two
the word meant, which is the evidence that the name had failed.

**Decision:** three renames and one new seam.

1. `HybridQueryAnalyzer` → `DecisionQueryAnalyzer`. The knob value it serves
   (`NORA_QUERY_ANALYZER=decision`) is unchanged, so no configuration moves.
2. The routing policy is `ConfidenceRoutedQueryAnalyzer`, spelled identically
   as the class, as `--arm confidence-routed`, and as
   `NORA_QUERY_ANALYZER=confidence-routed`. One spelling in three places.
3. Arms are **named, not numbered**, in this strand's records. The numbering
   had already collided: both this arm and the Kev fine-tune were "arm 4".

**Rejected: one `decision` value plus a floor knob.** A floor of 0.0 *is* the
unrouted policy, so the two policies genuinely are one implementation with one
parameter, and a 4-line subclass is the whole difference. It was rejected
anyway, because an administrator reading a configuration would have had to infer
the policy in force from the value of a second knob — and the specific
confusion this decision exists to fix was a policy that could not be read off
its name. The implementation stays shared: `DecisionQueryAnalyzer` holds the
logic with `confidence_floor=0.0`, and the subclass exists to name the policy.

**Rejected: routing per query.** Each answer carries its own confidence and
both questions travel in one request, so per-field routing costs nothing and
is strictly more informative. It also turned out to matter — see D-DRAFT-26.

**Also decided: the floor's default is 0.50 and the code says why it is soft.**
0.50 is the value the in-sample sweep peaked at. The resolver raises on a
non-number or anything outside `[0.0, 1.0]`, the docstrings on the resolver
and the class both state that the number was selected on the 50 samples it was
scored on, and the arm prints the caveat with every result rather than relying
on anyone to repeat it. A floor above 1.0 would route every field to the rules
while still labelling the output as routed, which is the design §7
silent-degradation shape arriving through a knob.

**Invariant added:** routing is not a fallback. `fallback_count` means the
provider was unreachable and the arm is contaminated; a routing means the
provider answered and the policy declined the answer. Conflating them would
make a healthy routed run trip `classify_cli`'s D-DRAFT-20 refusal, and would
let a dead provider hide inside a plausible coverage figure.

### D-DRAFT-26 — A computation over saved output is not a measurement

**Date:** 2026-10-20
**Context:** The `hybrid` row — 0.860 accuracy, 0.068 weighted cost — was
computed offline from the `decision` arm's saved per-sample confidences rather
than measured. When the same policy was implemented and run, the accuracy and
the weighted cost reproduced to three decimals and the `query_type` coverage
matched at 28 %. The arithmetic was right.

**What it missed:** scope accuracy. The offline computation routed
`query_type` only, because `query_type` was the column the question was about.
The shipped arm routes per field, and **scope accuracy rises from 0.760 to
1.000 at every floor from 0.30 up** — the model's scope answers are precisely
its low-confidence ones, so the floor discards them without being told to.
That is the cheapest win in the whole measurement and it sat unseen for a day
inside a row that looked complete, with a `—` in the scope column that read as
"not applicable" rather than "not computed".

**Decision:** a number computed over saved output may decide *whether to build
something*. It may not be recorded as what an arm does. Where one appears in a
record, the column it did not compute is marked as uncomputed rather than left
blank, because a blank in a table of measurements reads as a measurement.

**Second finding, kept separate because it is unresolved:** floor 0.20 scores
0.840 while routing 80 % of `query_type` to the model; floor 0.50 scores 0.860
while routing 28 %. The gap is one sample on n=50, which this set cannot
resolve — but the exposure difference is threefold. Which is better depends on
whether the objective is the best number on this set or the most contact with
the decision model on real traffic. **The strand has not said which, and the
default should not be defended on the strength of a one-sample difference
until it does.** For a strand whose stated purpose is building expertise
rather than winning at the first shot, the honest answer is probably 0.20.

### D-DRAFT-27 — `NORA_QUERY_ANALYZER` stays unset on served stacks

**Date:** 2026-10-20
**Context:** D-254 makes SIRA select-synth the serving path. On that path the
sira-query service reads only `intent.mnos` and `intent.releases` from the
analyzer's `QueryIntent` (`sandbox/sira_query/scope.py`, `resolve_cells`). The
decision model owns `query_type` and `doc_type_scope` (D-DRAFT-7), and only the
NORA `QueryPipeline` consumes those two fields. D-254 disables that pipeline in
current builds.
**Decision:** leave `NORA_QUERY_ANALYZER` unset (the `rules` default) on every
served stack until one of two things changes: the decision model owns a field
the serving path reads (for example `mnos` as one `noul` per candidate), or the
NORA pipeline serves.
**Why:** enabling `decision` or `confidence-routed` on a served stack adds one
`/v1/systemone` round trip per query, and the serving path discards the
result. The cost is real and the benefit is zero. The measurements in
`phase-3-measurement.md` stay valid for the NORA pipeline; they say nothing
about SIRA serving quality. TODO (strand owner): confirm that "until the model
owns scope" is the intended re-entry condition, rather than only "until the
NORA pipeline serves".
**Consequences:** the knob is an experimentation and evaluation surface
(`eval.classify_cli`, a trial stack running the NORA pipeline), not a serving
control. The commented `NORA_QUERY_ANALYZER` blocks in
`docker/env.sira-query.example` and `docker/env.nora-web.example` say so. The
sira-query `/healthz` key `query_analyzer` reports the arm actually built, so a
served stack with the knob set is visible without reading its env file.

### D-DRAFT-28 — The cross-doc signal counts distinct plan ids, not alias hits

**Date:** 2026-10-20
**Context:** `MockQueryAnalyzer._classify_query_type` counted `_PLAN_ALIASES`
hits as the cross-doc signal. A query that names one plan through two of its
own aliases scored two signals. Found while authoring the dev set (sample c04)
and held unfixed during the phase-1 measurement (D-DRAFT-24). Fixed in commit
`18e8ff4`.
**Decision:** the cross-doc signal counts distinct plan ids. The count comes
from `_extract_plan_ids`, which already de-duplicates by plan id, rather than
from a separate alias count.
**Why:** one plan named through two aliases routed a within-plan lookup to
`CROSS_DOC`. That class widens `top_k` and sets the BM25 weight to 0.0, so the
error changed retrieval, not only a label. On `classify-dev-v1` the rules arm
moved from 0.720 to 0.760 accuracy and from 0.136 to 0.112 weighted cost.
Exactly two samples changed, both from wrong to right (c02, c04), and none
regressed.
**Consequences:**
- `MockQueryAnalyzer` output changes for such queries. This also changes the
  NORA `QueryPipeline` classification output, including the golden-eval
  Stage-2 `pipeline`-mode numbers; a Stage-2 comparison across this commit is
  not like for like.
- The phase-2 identity gate (knob unset → `MockQueryAnalyzer`) still holds as a
  statement about which class is built. It no longer means "output identical
  to the pre-strand code" for queries of this shape.
- SIRA serving is not affected: it reads only `intent.mnos` and
  `intent.releases` (D-254, D-DRAFT-27).
