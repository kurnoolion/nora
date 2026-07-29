## D-DRAFT-1 — Batched doc-enrichment under a dual token budget; batch = transport, never state

**Context.** Single-req enrichment = 23k+ LLM calls per full pass —
throughput-bound, and each call repeats the full prompt header. The
64k-token LLM context invites packing many requirements per call, but
sizing scans showed the RESPONSE side (p95 86 tokens/req as strict
JSON) can overflow its reserve long before a 50k prompt cap is
reached when requirement texts are small.

**Decision.** Pack whole requirements per batch under a DUAL budget:
prompt side (cap 50k tokens, default; header measured by composing
the actual template + taxonomy block, req text via a chars/token
heuristic, default 3.5) AND response side (context − cap = reserve;
reqs/batch ≤ reserve / resp_tokens_per_req, default 90). Batch closes
when either budget would be exceeded. Requirements are never
fragmented; one too big for the budget ships as a solo oversized
batch, warned. Output contract: strict req_id-keyed JSON, every
packed id present, ≤10 phrases/req (the cap makes the response bound
enforceable). Missing/errored reqs re-queue into fresh batches
(bounded, default 2 rounds) then fail per-req. Batches are TRANSPORT:
per-req trace rows remain the resume/state unit; batch_id is metadata;
per-batch sizes log to batches.jsonl.

**Why.** Dual budget because the measured numbers prove either side
can bind first. Whole-req packing keeps the contract simple and the
LLM's per-req context intact. Per-req resume keeps the adapter's
existing trace/resume semantics untouched — a batched run interrupts
and resumes identically to a legacy run. Rejected: per-batch resume
(a failed batch would re-run answered reqs), tokenizer dependency for
exact counts (heuristic + margin is enough).

**Consequences.** resp_tokens_per_req and chars_per_token are
calibration constants (env-tunable) — a model with much longer
answers needs re-measurement. Trace rows no longer carry per-req
raw_response (batch-level in batches.jsonl instead). Auto-activation
by template shape ({requirements} present) means legacy prompts keep
the old path byte-identically.

## D-DRAFT-2 — Per-MNO prompts, per-plan taxonomy attachment, one plan per batch

**Context.** The single SIRA doc prompt was derived when only one
MNO's corpus existed; other MNOs' cells enrich with a prompt patterned
on the wrong subdomain mix and vocabulary. NORA's per-plan feature
taxonomies (feature/key-concepts/keywords, ≤5k chars) existed but
were invisible to enrichment.

**Decision.** One derived prompt per MNO (derive-sira-prompts skill,
MNO-scoped scan; doc_requirement_<MNO>_<ver>.txt), resolved per cell
at ingestion by the cell's MNO (env NORA_SIRA_DOC_PROMPT_DIR; loud
fallback to the config prompt). Prompt composition: per-MNO corpus
overview + the plan's WHOLE taxonomy file with a fixed prologue +
task instructions + packed requirements. Batches group by (MNO,
release, plan) so one taxonomy block serves the whole prompt; no
small-plan coalescing in v1 (≥1 batch per plan accepted; per-batch
size logging exists to revisit from data). Missing taxonomy →
omit block, warn, enrich anyway.

**Why.** Prompts should pattern the LLM after the corpus they enrich
— per-MNO is the natural unit (corpora differ per MNO; releases of
one MNO are near-duplicates). Taxonomy-per-plan is the tightest
domain context available and is already maintained per plan by NORA.
One-plan-per-batch keeps the prompt contract simple (one taxonomy,
one plan context). Rejected: multi-plan batches with multiple
taxonomy blocks (packing gain not yet proven needed).

**Consequences.** Per-MNO prompts contain real MNO vocabulary — they
live in `customizations/prompts/`, committed to the company-internal
remote only under the D-062 pre-push-hook trust boundary (amended
2026-07-24; supersedes the original "runtime artifact, never
committed" posture — both docker images COPY customizations/ at
build, so containers resolve them at /app/customizations/prompts and
a prompt update requires an image rebuild). New-MNO
ingestion gains a standard step: run the skill for that MNO.
Query/relevance prompts are also generated per MNO but the
query-time service loads a single pair — per-cell selection there is
deferred.

## D-DRAFT-3 — Combined staging: both prompt levers ship in one re-enrichment

**Context.** Two levers change together in this strand: the
enrichment prompt (overview + taxonomy + batching) and the taxonomy
CONTENT (regenerated with the overview-primed generation prompt).
The scorecard (D-164) attributes improvements to a lever only if
levers change one at a time — but it measures against expert
remove-records, and none exist yet (eval loop not run).

**Decision.** Ship both levers in ONE combined re-enrichment; the
result is the new baseline. From the first eval-loop campaign
onward: one lever per iteration, scorecard attributes each change.

**Why.** A staged rollout now would measure against nothing firm
(soft signals only — phrase churn, spot checks). Batching makes
re-enrichment passes cheap, so the option to iterate lever-by-lever
later stays open. Rejected: staged rounds now (calendar cost for
un-measurable attribution).

**Consequences.** This strand's quality delta is unattributed by
construction — accepted. The eval-loop strand inherits the job of
establishing the baseline against the combined result.

## D-DRAFT-4 — Corpus overview: derived once per MNO, consumed by both pipelines

**Context.** The AI-scanned corpus overview primes the SIRA doc
prompt. NORA's taxonomy-generation prompt (code:
core/src/taxonomy/extractor.py) sees only TOC headings + metadata —
no corpus context at all — and its output now feeds enrichment, so
its quality compounds.

**Decision.** The derive-sira-prompts skill writes the overview as a
standalone per-MNO artifact (corpus_overview_<MNO>_<ver>.txt) beside
the prompts. NORA's FeatureExtractor gains an OPTIONAL corpus-context
input (config knob → per-MNO file, resolved by the doc's MNO):
present → inserted as a "Corpus context" section; absent → today's
prompt byte-identically (fail-soft). (Implementation = slice 5;
includes the taxonomy-stage cache question: fingerprint currently
excludes the overview file — first regen needs --force or the
fingerprint gains the overview hash.)

**Why.** Derive once, consume twice — one authoring flow, no drift
between the two pipelines' understanding of the corpus. A second
skill deriving a second overview would double maintenance for the
same content. Optional + fail-soft keeps NORA's pipeline independent
of SIRA-side artifacts.

**Consequences.** The overview becomes shared infrastructure: its
regeneration cadence (on material corpus change) now affects BOTH
enrichment and taxonomy quality. Taxonomy MODULE.md must document
the new optional input when slice 5 lands.

## D-DRAFT-5 — Taxonomy cache fingerprint includes corpus-overview hash

**Context.** Slice 5 made per-MNO corpus overviews an input to the
taxonomy stage's extraction prompt. The stage is cached on a corpus
fingerprint (D-DRAFT-9, strand multi-mno-nora lineage) that hashed
only the contributing parse trees — so adding or editing an overview
would NOT flip the fingerprint, and a re-run would silently serve the
cached, context-less taxonomy. The design doc left the choice open:
require `--force` after overview changes, or fold the overview into
the fingerprint.

**Decision.** `_corpus_fingerprint` additionally hashes every
`corpus_overview_*.txt` (name + bytes) under
`$NORA_TAXONOMY_OVERVIEW_DIR`. Adding, editing, or removing an
overview re-derives the taxonomy on the next run; `--force` remains
available but is never required for correctness.

**Why.** Prompt inputs are cache inputs — a cache key that omits one
of them serves stale output by construction. The combined-staging
plan (D-DRAFT-3) depends on regenerated taxonomies actually being
regenerated; an operator forgetting `--force` would invalidate the
whole re-enrichment baseline without any error surfacing.

**Consequences.** Any overview-file touch triggers a full (LLM-driven,
non-deterministic) taxonomy re-derivation — deliberate: overviews
change rarely and only when regeneration is wanted. Hashing is
slightly over-broad (an overview for an MNO absent from the corpus
still busts the cache) — accepted for simplicity. The fingerprint
now depends on an env var's contents: same trees + different
`NORA_TAXONOMY_OVERVIEW_DIR` ⇒ different fingerprint, which is the
correct reading (different prompt inputs, different taxonomy).

## D-DRAFT-6 — Taxonomy extracts each plan from its newest release only

**Context.** Taxonomy is a global stage over every cell's parse trees,
but its output layout (`out/taxonomy/<plan_id>_features.json`) and the
downstream per-plan lookup are release-blind. With multiple releases
ingested, the same plan was LLM-extracted once per release and the
copies silently overwrote each other in incidental lexicographic path
order — which copy survived was accidental (observed on the work PC as
a March/July mix after a crashed run). Options: (a) extract only the
newest instance of each plan; (b) per-cell output
`out/taxonomy/<MNO>/<REL>/` plus a cell-aware SIRA lookup; (c) keep
extracting everything with deterministic oldest→newest overwrite.

**Decision.** (a): dedupe to the newest instance of each
`(MNO, plan_id)` before extraction — file-level in
`_select_newest_trees`, then again at plan-unit level after
`split_tree_by_plan` (chapter-per-plan docs have an empty tree-level
plan_id, invisible to file-level dedup). "Newest" = release directory
name parsed MMMYYYY → `YYYYMM` (`Jul2026` → `202607`) for chronological
comparison, lexicographic fallback for other naming, file-name
tiebreak. Superseded copies are counted in stage stats and never sent
to the LLM. The cache fingerprint hashes the selected set only.

**Why.** Releases of one MNO are near-duplicates and the consumer keys
on plan_id alone, so per-cell fidelity (b) isn't consumable without
SIRA-side changes, and (c) pays full LLM cost for outputs that get
overwritten. (a) halves LLM calls where releases overlap — fewer
chances to hit a flaky endpoint — and makes the surviving copy correct
by construction. Plain lexicographic name comparison was rejected
after the user's correction: month-name prefixes sort Jul2026 before
Mar2025.

**Consequences.** Older releases contribute nothing to the taxonomy —
acceptable while releases are near-duplicates; revisit if a plan's old
release diverges materially from its newest. An edit to a superseded
tree does not re-derive (fingerprint covers selected files only).
Release dirs not in MMMYYYY form fall back to alphabetical comparison,
which may not be chronological — new naming conventions must extend
`_release_sort_key`.

## D-DRAFT-7 — Taxonomy resilience: per-unit ledger, retry = re-run, no empty successes

**Context.** The taxonomy stage ran one unguarded loop over all trees:
a single sporadic LLM/server error killed the whole run,
`taxonomy.json` and the cache fingerprint were only written at the
end (a crash lost all progress), and an unparseable LLM response was
saved as an empty-but-valid features file. SIRA's enrichment side
already had a retry mechanism; the user asked for equivalent
capabilities: proceed-past-errors, resume, and failed-entry cleanup
with "some kind of retry-failed flag".

**Decision.** Per-unit fail-soft + resume via
`out/taxonomy/extraction_state.json`: each unit records status
(`ok`/`failed`), source-tree hash, and error; the file is rewritten
after every unit so a hard kill (docker stop, OOM) still resumes.
On re-run, unchanged `ok` units are reused and `failed` units retry
automatically — re-running the same command IS the retry mechanism;
no `--retry-failed` flag exists. A partial run consolidates what
succeeded and returns WARN (`TAX-W004`); the corpus fingerprint is
stamped only on a zero-failure run; all-units-failed is a FAIL.
Unparseable responses raise `LLMParseError` (extractor) instead of
returning empty `DocumentFeatures`; stale/orphaned features files are
deleted on re-derive. Prompt inputs (corpus overviews) are hashed into
the per-unit reuse key, not just the stage fingerprint.

**Why.** A failed unit has no valid output to protect, so retrying it
by default is always correct — a flag would just be a way to forget
it. Stamping the fingerprint on a degraded run would cache-lock the
degraded taxonomy (the exact failure mode that motivated D-DRAFT-5's
"prompt inputs are cache inputs" rule). Empty-success responses
poisoned both the cache and consolidation with silently-missing
features.

**Consequences.** `extraction_state.json` is a persistent stage
artifact operators may inspect (ok/failed histogram is in the runbook)
and occasionally surgically edit (done once: invalidating heading-less
mno-b units by dropping `#`-keyed entries). "Re-run until failed=0" is
now the documented operating loop for sporadic endpoint errors.
`_parse_response` raising instead of returning empty is a behavior
change for any future caller — callers must treat it as retryable.

## D-DRAFT-8 — Plan-unit taxonomy: split chapter-per-plan docs, headings join by ancestry

**Context.** One MNO publishes a single requirement doc where each
chapter is a plan: tree-level plan_id is empty, per-req plan_id is set
(D-DRAFT-1 lineage), 87 plans in one file. Unsplit, taxonomy produced
one `_features.json` (empty prefix — unfindable by SIRA's per-plan
lookup) from one whole-doc LLM call whose 200-line outline cap dropped
everything past the first ~200 headings. Extraction sends only
section-number + title outline lines, so heading nodes are the main
feature signal; but headings carry no req_id (hence no per-req plan),
and leaves in this corpus carry `parent_section` but no
`section_number`. Dropping unplanned nodes discarded 870 of 4942
nodes. Two attachment strategies were considered: document-order
backward-fill vs. an ancestry join.

**Decision.** The unit of taxonomy extraction is a plan, not a file.
`split_tree_by_plan` (taxonomy public surface) splits multi-plan trees
into per-plan subtrees; single-plan trees pass through unchanged (a
blank tree plan_id is promoted from its requirements). Headings attach
to the majority plan among plan-bearing reqs whose `parent_section`
sits at or under the heading's `section_number`; only nodes enclosing
no plan-bearing requirement drop (logged). Document order is preserved
per group; `plan_name` becomes the chapter heading title. The pipeline
stage dedupes units by `(MNO, plan_id)` (D-DRAFT-6) and ledgers them
as `<tree-relpath>#<plan_id>` so chapter-plans fail/resume/retry
independently; single-plan trees keep plain `<tree-relpath>` keys
(existing ledgers carry over).

**Why.** Per-plan calls fix all three failures at once: findable
output names, focused outlines under the 200-line cap, correct
consolidation attribution. The ancestry join was chosen over
document-order backward-fill after run-length diagnostics on the real
tree: chapters interleave multiple plans and trailing headings precede
the next chapter's leaves, so order-based fill misattributes at every
chapter boundary, while the `parent_section` join is position-independent
and was fully populated (4072/4072 leaves). Verified on the work PC:
drops fell 870 → 115, all requirement-free tail sections.

**Consequences.** mno-b yields ~87 LLM calls instead of one (that cost
is the fix). Heading attachment requires populated `parent_section` —
a profile that omits it degrades to leaf-only outlines (headings drop,
logged). Prose sections without req_ids remain invisible to
taxonomy/enrichment by design; their text stays reachable via section
nodes / `build_context_string`. A multi-plan chapter's top heading
goes to its majority plan only.

## D-DRAFT-9 — Post-interruption recovery: heal-torn invariant repair + mode-aware lane repair flags

**Status**: Draft · **Date**: 2026-07-29.

**Context:** A power outage killed a long doc-enrichment run mid-write.
SIRA's resume is doc_id-keyed over append-only JSONL traces, but a hard kill
loses each open file's buffered tail independently, leaving (a) torn
half-written trailing lines — which crash the end-of-run merge's bare
`json.loads` — and (b) a broken kept↔enrichment pairing: a `trace.kept` row
whose enrichment row was lost makes the doc permanently "done" with its
enrichment silently gone; the reverse merges duplicate phrases on re-enrich.
Operators also had to hand-run per-cell `retry-failed` before every resume.

**Decision:** (1) New `sira_incremental heal-torn` subcommand: drop
unparseable lines from every `*.jsonl` in a stage run dir (shard files
included), then repair the doc-enrich invariant in BOTH directions — evict
kept-without-enrichment, drop orphan enrichments; `trace.failed` rows
untouched (retry-failed's jurisdiction). (2) `sira_lane --heal-torn` and
`--retry-failed [--include-all-filtered]` run the repairs per target cell
before the lane (heal first, so retry-failed parses every row), and are
skipped with an explicit note under `--wipe-all-derived` because the adapter
wipes `runs/` — repairs would be silently discarded.

**Why:** Torn-line dropping alone is insufficient — per-file buffers flush
independently, so either side of the kept/enrichment pair can survive alone;
only the two-way invariant repair makes resume trustworthy after a hard
kill. The repair lives in `sira_incremental` (the established home of trace
surgery, D-153's colocation rationale); the lane flags are thin delegating
conveniences, preserving D-153's subcommand-over-ingest-flag boundary.
Mode-awareness prevents the misleading "healed N → immediately wiped"
sequence. Alternative considered: manual host-side torn-line script →
rejected as unrepeatable and blind to the invariant damage.

**Consequences:** Run-dir trace files are no longer strictly append-only
from the operator's view — heal/retry rewrite them in place (inspect before
repairing). `heal_run_dir` must track any future change to the kept/
enrichment write pairing in `enrich_batching`. One-command recovery
(`sira_lane --heal-torn --retry-failed`) applies to incremental runs only;
full rebuilds ignore the flags by design.
