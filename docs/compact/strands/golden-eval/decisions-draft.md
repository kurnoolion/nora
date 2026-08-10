## D-DRAFT-1 — Golden eval samples as per-sample JSON under `<env_dir>/eval/golden/`, with the NFR-9 artifact triple

**Context:** The golden eval set (50 → 200 expert-curated samples) needs a
persistent shape. The existing eval path loads user questions from Excel
workbooks (`<env_dir>/eval/*.xlsx`, D-022). Samples carry proprietary content
(queries, req_ids, golden responses) and are authored concurrently by multiple
experts across environments (dev PC / work PC / team members).

**Decision:** One JSON file per sample at
`<env_dir>/eval/golden/samples/<sample_id>.json`, schema owned by
`eval/golden.py` (`GoldenSample`): sample_id, created_by, timestamps, area
tag, query, `ground_truth` entries (req_id + optional `(mno, release, plan)`
qualifiers, plus a `source` field — `picker` / `direct` / `retrieval` — that
makes the QC template's ≥1-independently-sourced-entry check computable),
golden_response + curation meta, status
(`draft | stage1-ready | golden-ready`). Run artifacts under
`<env_dir>/eval/golden/runs/<run_id>/`. Never in the repo. As the NFR-9
triple for the new artifact type: error-code prefix **`GEV-`** (registered in
pipeline's `CODES` catalog — a deliberate artifact-family prefix inside the
eval module, alongside `EVL-`), a GEV compact run report (counts, recall
aggregates, judge mean/median per stack, cross-stack delta — no proprietary
content), and a fixed-field QC template (ids resolve; qualifier coverage; ≥1
independently-sourced ground-truth entry per sample; single judge version per
report).

**Why:** Per-file JSON gives conflict-free concurrent authoring, diff-able
cross-environment sync, and save-time validation in the Eval Studio — an
Excel workbook gives none of these and can't hold nested qualified ground
truth cleanly. Rejected: extending the `.xlsx` loader (parallel semantics in
one spreadsheet schema); one big JSON file (merge conflicts across experts).

**Consequences:** Two eval-set schemas coexist — legacy `EvalQuestion`
(built-ins + `.xlsx`) and `GoldenSample`; they never mix. Web writes samples
only through `golden.py`. `GEV-` breaks the strict one-prefix-per-module
convention (eval now has `EVL-` + `GEV-`); the catalog entry documents it as
an artifact-family prefix.

**Amendment (2026-08-06):** The golden store's home must survive the
docker-distro serve topology: promoted `serve/<label>/` snapshots are
immutable and GC-able, so samples cannot live inside the label a stack
mounts as its env dir. Compose gained `GOLDEN_DIR` — a pooled host dir
mounted over `<env>/eval/golden` on nora-web AND nora-pipeline (one sample
set across A/B stacks; batch CLI reads the same store), defaulting to
`<NORA_ENV_DIR>/eval/golden` so single-build setups are unchanged. Rejected:
promote.sh carrying samples forward (live data inside immutable snapshots
muddies rollback); host-local compose override (invisible to the repo).

---

## D-DRAFT-2 — Stage-1 recall scored black-box via the sira-query service HTTP API, per stack

**Context:** Stage-1 scores % of ground-truth req_ids present in retrieved
results. The SIRA-only lane does not run through `QueryPipeline` — it's the
sandbox sira-query service. Two serve stacks are live (pre-PE and post-PE);
the eval must compare them.

**Decision:** `GoldenRunner.run_stage1` POSTs the sample query to
`POST /sira-query` on a configured stack URL and computes recall from
`results[].req_id` against the sample's ground truth (strict cell-qualified
match when qualifiers are present, req_id-only otherwise). Per-hit ranks are
recorded so recall@5/@10 derive from the same run. One run per stack URL is
the release A/B. No in-process reconstruction of SIRA retrieval inside eval.

**Why:** The metric should measure what the stack actually serves — enrich
model, overlay state, fusion config and all. An in-process reconstruction
would drift from the service and silently measure something else. Black-box
HTTP also makes the pre-PE/post-PE comparison trivial (two URLs) and keeps
eval decoupled from sandbox internals. Rejected: importing sandbox retrieval
code into eval (couples core to sandbox across the D-111 boundary).

**Consequences:** eval gains a runtime HTTP service edge (same kind as web's;
documented in MODULE.md, not an import). A stack must be up to run Stage-1 —
failures are fail-loud `GEV-` errors, never silently skipped samples. Scores
are only comparable across runs when the stack's retrieval config is stable;
the run report records the stack's `/healthz` snapshot for provenance.

---

## D-DRAFT-3 — Stage-2 regeneration reuses `pinned_chunk_ids` — no new synthesis path

**Context:** Stage-2 must regenerate a response from Stage-1's retrieved
chunks "using the same prompt used by nora-web" and judge it against the
expert's golden response. `QueryPipeline.query(..., pinned_chunk_ids=...)`
already skips retrieval stages and synthesizes only from named chunks
(`req:<req_id>` ids) — the exact mechanism the Test page's SIRA lane and the
D-049 disambiguation path use.

**Decision:** `GoldenRunner.run_stage2` maps Stage-1's retrieved req_ids to
`pinned_chunk_ids=["req:<id>", ...]` and calls the production `QueryPipeline`.
The candidate response is then judged against `golden_response`. No
eval-specific synthesis prompt or code path exists.

**Why:** Same-prompt-as-production holds by construction, not by keeping two
prompts manually in sync — any prompt or knob change to the web lane is
automatically what eval measures. Rejected: a standalone eval synthesizer with
a copied prompt (guaranteed drift, and the drift would be invisible precisely
when it matters).

**Consequences:** Stage-2 requires the NORA store containing the ground-truth
chunks (`req:<id>` lookup) in addition to the stack used for Stage-1.
Eval-side and web-side synthesis sharing one path becomes an invariant —
documented in eval's MODULE.md; a future web-only synthesis fork would
invalidate judge-score comparability and must be treated as a design change.

---

## D-DRAFT-4 — LLM judge for Stage-2 similarity; versioned judge prompt; amends eval's rule-based key choice

**Context:** eval's standing key choice: "Scoring is rule-based, not
LLM-judged — avoids the judge-LLM cost and keeps eval runnable offline."
Stage-2 needs semantic similarity between a regenerated response and an
expert-curated golden response — beyond what rule-based checks can measure.

**Decision:** Stage-2 similarity is scored by an LLM judge running on the
local on-prem provider (separately injectable `LLMProvider`, defaulting to
the synthesis provider). Score shape: 0–10 similarity plus short
missing/contradicting-point lists (lists stay in the `<env_dir>` run report;
compact summaries carry numbers only). The judge prompt is a versioned,
committed artifact (`core/src/eval/prompts/judge_v<N>.txt`, generic wording —
it sees proprietary content only at runtime); every report records the
version, and scores are comparable only within one version. The rule-based
path stays for the legacy 5-metric questions.

**Why:** The half of the original choice worth keeping is
offline/no-external-calls — an on-prem judge preserves it; the rule-based
half can't assess response similarity. Versioning the prompt is what makes
longitudinal comparison honest: a silent judge-prompt edit would masquerade
as a quality regression or improvement. Rejected: embedding-similarity
scoring (blind to factual contradiction); external judge API (violates the
offline posture); judge frameworks (Ragas, DeepEval, promptfoo) — the
Stage-1 metric is a trivial set intersection and the judge call is one
`LLMProvider` invocation, while the frameworks bring LangChain-style/Node
dependency weight and their own prompts that would fight the versioning +
provenance rules here. The judge prompt itself borrows their well-tested
**statement-decomposition pattern** (extract claims from both responses,
classify each covered/missing/contradicting, then score) — copy the idea,
not the dependency.

**Consequences:** eval's MODULE.md key choice is amended, not removed.
Judge-version discipline: changing the prompt means a new file version, and
cross-version score deltas are meaningless — the QC template enforces a
single version per report. A separately-configured judge model becomes a knob
the run report must record.

---

## D-DRAFT-5 — Eval Studio is a web router, not a new module; shared req-tree helper; web → eval edge declared

**Context:** The expert one-stop-shop (sample CRUD, MNO → Plan → Release
ground-truth picker, Stage-1 preview, Stage-2 curation chat) needs a home.
Candidates: a new top-level module, or a router inside web. The picker needs
req_browser's tree loading, currently module-private functions.

**Decision:** A new router `routes/golden_eval.py` (`/eval-studio`) inside
web, composing existing surfaces: team-mode gate (experts admitted, delete
admin-gated), per-expert attribution, app.state stores, the sira-query
service edge for previews, and eval's `golden.py` for all sample I/O.
req_browser's `_load_tree_flat` / `_build_tree_hierarchy` / cell discovery
are lifted into a shared `web/req_tree.py` used by both routers. web's
Depends-on gains [eval] — making explicit the edge eval's Depended-on-by
already claimed.

**Why:** Every capability the studio needs already lives in web; a new module
would duplicate the gate, templates, config resolution, and the service edge
for one page family. The helper extraction is the minimal seam — duplicating
tree-loading logic in two routers is how the picker and the Requirement
Browser drift apart. Rejected: a standalone module (boundary without a
distinct contract); embedding the studio in the playground router (already
the largest route file, unrelated concern); off-the-shelf annotation
platforms (Label Studio, Argilla) — the custom 20% (corpus-aware
MNO → Plan → Release picker over NORA's parse trees, sira-query preview,
Stage-2 curation chat) is exactly what they don't provide and would be built
as custom frontend glue anyway, while the 80% they do provide (forms,
storage, users, gating) is already nearly free in the existing web stack;
they also add heavy services (Django+Postgres+npm / Elasticsearch) touching
proprietary content on a locked-down host. Revisit trigger: eval campaigns
multiplying beyond this feature (many campaign types, inter-annotator
agreement, dozens of experts) — at that scale re-evaluate Argilla.

**Consequences:** web grows another gated surface — the team-mode whitelist
must admit `/eval-studio`. The web ↔ eval relationship is now bidirectional
at the doc level (web imports eval's schema; eval's runners are invoked from
web's eval routes) — acceptable as-is because eval imports nothing from web;
a true import cycle would force a schema extraction. MAP.md gains the
web → eval edge at next regen.

---

## D-DRAFT-6 — Eval Studio reads requirement texts from parse trees, not the vector store

**Context:** The studio needs requirement text in three places: picker rows,
direct-entry validation (`find_req`), and the Stage-2 curation-chat context.
Both sources exist: the built vector store (chunk text, needs the vectorstore
stage + heavy pipeline build) and the parse trees under `out/parse/`
(available from the parse stage onward, plain JSON).

**Decision:** All three read parse trees via the shared `web/req_tree.py`
loader. The vector store is touched only where synthesis genuinely needs it —
the Stage-2 batch runner's `pinned_chunk_ids` path (eval side, not studio).

**Why:** Experts can author and curate as soon as parsing has run — no store,
no graph, no cold-start pipeline build behind a page load. Tree JSON reads are
cheap and per-cell scoped. Rejected: store-backed reads (couples the studio to
the built store's existence and the ~10s pipeline build; chunk text differs
from source text only by contextualization headers, which experts don't need).

**Consequences:** Curation-chat context is parser text, not chunk-builder
text — fine for grounding an expert's golden answer, but it is NOT the exact
string the synthesizer sees at eval time (that comes via pinned chunks). A
req_id present in the store but missing from parse trees (or vice versa)
surfaces as a studio-vs-runner discrepancy — GEV-E001/E003 make it loud.
`find_req` scans every cell's trees per lookup; if corpora grow enough to make
that slow, an index cache inside req_tree is the fix, not a store switch.

**Amendment (2026-08-06):** Real corpora hit both predicted costs at once —
per-entry corpus-wide `find_req` scans during editor renders, executed on
the event loop by async handlers, froze the entire app for minutes. The
index cache this draft anticipated is now built (`req_tree.load_tree`,
mtime+size-keyed; safe because the pipeline is the sole tree writer), plus
`find_req` takes cell qualifiers (qualified entries scan one cell). New
posture choice beyond the cache: Eval Studio handlers are sync `def`
(FastAPI threadpool) — a deliberate deviation from the router's async style,
because these handlers do blocking work inline (corpus reads, sira-query
HTTP, curation LLM calls) rather than delegating to background jobs like
query.py. Any new studio handler doing blocking work must stay sync.

---

## D-DRAFT-7 — Core permanent-refusal fallback: provider decorator + deliberate twin of the sandbox detection module

**Context:** baa3f14 (landed strand sira-enrichment-pe) gave the enrich lane
and the sira-query service a permanent-refusal fallback, but the synthesis
step — the one LLM call whose output the user reads verbatim — had none. The
team hit exactly that: a permanently-refused synthesis surfaced the refusal
notice as the answer. Core cannot import the sandbox detection module (D-111
boundary), and that module must stay flat-copyable into the SIRA clone.

**Decision:** `core/src/llm/refusal.py`: detection functions as a deliberate
twin copy of `sandbox/llm_refusal.py` (same rules, same
`NORA_LLM_REFUSAL_MARKERS`), plus `RefusalFallbackProvider` — an LLMProvider
decorator that retries a marker-prefixed, JSON-free response ONCE on a
fallback endpoint (`NORA_LLM_FALLBACK_BASE_URL/_MODEL/_API_KEY`).
`maybe_wrap_with_refusal_fallback` wraps at the two builder choke points:
web's query/chat LLM builder and golden_cli's `_build_llm` — synthesis, the
/test lanes, curation chat, and the eval judge all inherit one config. A
sync-guard test (`TestTwinSync`) compares the twins' function sources and
fails on drift. Partial config warns loudly instead of silently disabling.

**Why:** A protocol-level decorator covers every core lane through structural
typing — no per-call-site wiring, and Stage-2 eval stays on the identical
provider chain (D-DRAFT-3) by construction. Rejected: importing sandbox
(breaks D-111); relocating the canonical module to core (the SIRA-clone
flat-copy must stay dependency-free and core-free); per-lane fallback code
in web routes (three copies of the same logic). The twin-copy cost is
mitigated by the source-parity test rather than discipline alone.

**Consequences:** Three fallback config families now exist (enrich, sira-query,
core) sharing one markers var — each container's env file needs it. The twins
must change together; the sync test makes divergence loud. Taxonomy remains
the only lane without refusal fallback (deferred since baa3f14). Provenance:
this draft belongs to the sira-enrichment-pe lineage — carried here because
the session was bound to golden-eval; note at promotion time.

**Amendment (2026-08-06, commit 95e6538):** Scope corrected from "two
builder choke points" to one. The pipeline's LLM stages (taxonomy, eval)
and the debug / miner CLIs construct providers via
`PipelineContext.create_llm_provider`, bypassing both wrap sites — so
taxonomy still had no coverage (the lane baa3f14 deferred as TBD). The
wrap now lives INSIDE `create_llm_provider` (construction proper moved to
`_construct_llm_provider`), which every pipeline stage, both debug CLIs,
and web's builder already funnel through; web's explicit wrap is dropped
as redundant, and `maybe_wrap_with_refusal_fallback` is idempotent so
double-wrapping stays safe. golden_cli keeps its own call — it builds
providers directly rather than through a PipelineContext. New invariant
(pipeline MODULE.md): constructing a provider outside
`create_llm_provider` opts out of refusal coverage. Config block added to
`env.nora-pipeline.example`. Refusal coverage is now complete across all
lanes — the taxonomy gap noted in this draft's Consequences is closed.

---

## D-DRAFT-8 — Synthesis answers carry a provenance epilogue naming the model that answered

**Context:** With a refusal fallback in place (D-DRAFT-7), two models can
answer queries and nothing user-visible said which one did. The team asked
for provenance on the answer itself.

**Decision:** Every synthesized answer ends with a blank line and
"Synthesized by <model>". `RefusalFallbackProvider` tracks `last_model` per
call, and the shared `answering_model()` helper (llm/base.py) prefers it over
the provider's static name — a fallback-answered call names the fallback
model. Stamped in BOTH synthesis paths (LLMSynthesizer and the /test SIRA
lane's select-synth call), appended after citation extraction. Mock providers
(no model name) stamp nothing. Deliberately NOT applied to the curation chat.

**Why:** The team reads answers, not response metadata — an epilogue is the
only placement that actually surfaces fallback engagement to them, and it
doubled as the deployment verification signal. Appending after citation
extraction keeps the stamp out of cited-req-id detection. The curation chat
is excluded because chat drafts get pasted into golden responses, and a
provenance line there would contaminate the golden text.

**Consequences:** The answer is no longer pure model output — anything
parsing answers must tolerate the trailing line. Stage-2 candidates carry the
epilogue while golden responses don't; judge_v1 instructs style-blindness,
but if real runs show the judge citing it, strip it in run_stage2 before
judging (flagged in the journal). Two stamp sites exist because select-synth
bypasses LLMSynthesizer — a third synthesis path would need the same stamp.

## D-DRAFT-9 — SIRA corpus requirement rows stamp bare plan_id, never plan_name

**Context**: Enrichment resolves each plan's taxonomy block as
`<plan_id>_features.json` from the corpus row's `**plan**:` stamp (via
`plan_of()`). Heading-mode requirement rows (one-doc-per-plan corpora,
mno-a shape) stamped plan_name instead — a back-compat leftover from the
per-req-plan work, predating taxonomy blocks in enrichment. On mno-a,
where plan_name ≠ plan_id, every taxonomy lookup missed and the entire
enrichment build ran without taxonomy context, silently (42 warnings,
one per plan). Nothing asserted the stamp value.

**Decision**: Requirement rows stamp the bare plan_id in BOTH detection
modes (heading mode: `tree.plan_id or tree.plan_name`; leading-id
tree-level fallback flipped the same way). plan_name remains only on the
composite doc/section rows (`plan_id / plan_name`). Stamp value pinned
by tests (36b08f7).

**Why**: Every machine consumer keys on plan_id (taxonomy lookup,
`_plan_matches`, corrections attribution). Composite req-row stamps were
rejected: the sira-query plan dropdown lists req-row stamps verbatim and
`_plan_matches` documents "requirement rows stamp a single value" — bare
plan_id fixes the lookup with zero consumer changes. An enrichment-side
name→id mapping was rejected as a second source of truth for a
one-line stamping fix.

**Consequences**: Every heading-mode row's text changes → content-hash
flip → full re-enrichment of those cells on next build (needed anyway to
get taxonomy blocks into prompts). Plan dropdown shows plan ids, not
names. Corpora built pre-fix carry enrichment generated without taxonomy
context until rebuilt. plan_name is display/BM25 vocabulary on composite
rows only — any future consumer wanting names on req rows must extend
the composite convention, not re-prefer plan_name.

**Provenance note for promotion**: belongs to the sira-enrichment
lineage (amends the per-req plan-stamp choice from multi-mno-sira's
D-DRAFT-1, promoted line); drafted here because the session was bound to
golden-eval.
