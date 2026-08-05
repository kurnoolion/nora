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
qualifiers), golden_response + curation meta, status
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
