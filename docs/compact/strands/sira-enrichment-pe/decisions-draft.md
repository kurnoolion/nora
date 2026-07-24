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

**Consequences.** Per-MNO prompts contain real MNO vocabulary —
runtime artifacts unless verified clean for commit. New-MNO
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
