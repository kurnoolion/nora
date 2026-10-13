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

### D-DRAFT-5 — A small purpose-trained classifier is a first-class arm, not a fallback

**Date:** 2026-10-13
**Context:** The directive frames the work as migrating decision-making to a
Jev-class model. Published counter-evidence on the category argues the
category is often the wrong tool for exactly this task shape.
**Decision:** The comparison carries a fourth arm — a small purpose-trained
classifier (200M-class, offline) — evaluated on equal footing with the
decision-model candidates, not as a consolation path.
**Why:** On tasks they are trained for, small classifiers match or beat
decision models while being cheaper, faster, and offline: a 200M DeBERTa
scored 89.01% at 54 ms against Jev's 86.35% at 348 ms on prompt-injection
detection; a 125M model hit 80.27% at 33 ms on content safety. The category's
headline selling point — calibration — is also softer than advertised: Jev
runs overconfident at 0.81 mean confidence against 0.74 actual accuracy
(ECE 0.074), and JEV-9B's published ECE of 0.0007 is computed against the
teacher's distributions, measuring closeness to Jev rather than accuracy
against truth (0.0759 independently). The survey's own selection rule is
"start with a small classifier if you have labels." NORA has 8 stable
`QueryType` classes and recorded real user queries (submit-time question
recording from the `ask-history` work), so labels are cheap to produce.
**Consequences:** Phase 1 must include a labelling step, which is new work the
directive does not currently budget. A classifier arm that wins would retire
the vLLM / DGX-Spark commitment in D-DRAFT-3 and D-DRAFT-4 entirely — the
cheapest available outcome, and the reason it must be measured rather than
assumed away. The cheapest outcome of all remains possible: `MockQueryAnalyzer`
already exists and may match the LLM without any new model.
