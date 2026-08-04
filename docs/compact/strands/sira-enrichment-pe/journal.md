## 2026-07-24 — Strand opened: design + slices 1–4 in one session

### Done this session
- Design pass complete (enrichment-pe-design.md): per-MNO prompts,
  per-plan taxonomy blocks, batched enrichment under a DUAL token
  budget (50k prompt cap; response reserve binds at ~155 reqs/batch @
  90 tok/req — calibrated by a 23,252-req scan), strict req_id-keyed
  JSON, per-req resume (batch = transport), one-plan-per-batch with
  per-batch size logging (coalescing rejected for v1), taxonomy source
  <env_dir>/out/taxonomy/<plan_id>_features.json, COMBINED staging for
  taxonomy-prompt cross-pollination (no curation exists; no measuring
  stick until the eval loop runs).
- Slice 1: sizing scanner (sandbox/scan_enrichment_stats.py, f5ff935).
- Slice 2: derive-sira-prompts skill + playbook MNO-parameterized;
  batched doc-prompt placeholder contract; standalone
  corpus_overview_<MNO>_<ver>.txt shared with NORA taxonomy extractor.
- Slices 3–4: sandbox/enrich_batching.py (packer/composer/parser/
  run_batched, 19 tests, clone-free) + batched-enrich.patch (adapter
  wiring, auto-activates on batched template, applies after
  per-stage-routing.patch — verified) + install_configs.sh + env knobs
  in env.sira-batch.example.

### In progress
- Slice 5, NORA half not started: FeatureExtractor corpus-context
  input + taxonomy-stage cache-fingerprint decision + taxonomy
  MODULE.md update.

### Next
- Slice 5 (NORA half), then work-PC sequence: git pull →
  install_configs.sh → Cline skill per MNO (3 prompts + overview each)
  → taxonomy regeneration → ONE combined re-enrichment with
  NORA_SIRA_DOC_PROMPT_DIR / NORA_SIRA_TAXONOMY_DIR set.

### Flags
- Taxonomy-stage cache fingerprint excludes the overview file — first
  regeneration needs --force, or fingerprint gains the overview hash
  (decide at slice-5 implementation).
- Per-MNO query/relevance prompts are generated but the query-time
  service loads a single pair — per-cell selection there is deferred.
- Real-batch validation pending: the batched path is unit-tested only;
  first work-PC run should start with one small cell.

## 2026-07-25 — slice 5 (NORA half) + prompts-are-customizations decision

### Done this session
- Slice 5, NORA half (commit 599e66a): `FeatureExtractor(llm, overview_dir=)`
  + `resolve_corpus_overview()` (highest-version glob); `{corpus_context}`
  section in the extraction prompt — byte-identical when absent (test-locked);
  `TAX-W003` registered; `run_taxonomy` reads `NORA_TAXONOMY_OVERVIEW_DIR`;
  cache-fingerprint decision resolved: overview files hashed into the corpus
  fingerprint (auto-bust, no `--force`); `taxonomy_cli --overview-dir` parity;
  taxonomy + pipeline MODULE.md updated; 6 new tests, suite 1541 green.
- Prompt-location decision (user, commit 9482921): per-MNO prompt artifacts
  (3 prompts + corpus overview per MNO) live in `customizations/prompts/`,
  committed to the company-internal remote only (D-062 pre-push-hook trust
  boundary) — supersedes the "runtime artifact, never committed" posture.
  Containers consume them at `/app/customizations/prompts` (images COPY
  customizations/ at build; prompt update ⇒ image rebuild). New
  `customizations/prompts/README.md`; SKILL.md + playbook output paths;
  env examples (`NORA_TAXONOMY_OVERVIEW_DIR` moved to the nora-pipeline
  example where it belongs); .gitignore comment; design doc + D-DRAFT-2
  consequences amended.
- Work-PC runbook settled in-session: pre-upgrade image snapshot via
  `push.sh` (+ local `:pre-enrichment-pe` rollback tags) → pull →
  install_configs → Cline derive-prompts per MNO (MNO-only, releases
  pooled) → commit prompts to internal remote → rebuild both images →
  taxonomy regen (`--start taxonomy --end taxonomy`) → combined
  re-enrichment starting with one small cell.

### In progress
- (nothing mid-flight in the repo — all strand code/docs slices 1–5 are
  committed; the remaining work is the user-run work-PC sequence)

### Next
- Work-PC run: snapshot images → pull → derive prompts ×3 MNOs →
  taxonomy regen → combined re-enrichment (one small cell first) —
  then eval loop as a separate strand.

### Flags
- `.claude/settings.local.json.bak` (untracked, dev PC) contains pre-scrub
  history strings and tripped the redaction gate when accidentally staged —
  delete it.
- Batched enrichment path still unit-tested only; first real-LLM run should
  start with one small cell (carried from 07-24).
- Per-MNO query/relevance prompt selection in the query-time service still
  deferred (carried from 07-24).

## 2026-07-27 — taxonomy hardening: resilience, multi-release semantics, plan-unit split

### Done this session
- (interim, unjournaled 07-25 commits) Fresh-env runbook `docs/runbook-fresh-env.md`
  (Phases 0–6: one-shot + incremental ingestion, SIRA-only Phase 4–6 path);
  derive-sira-prompts skill/playbook operational hardening (parse-layer-only,
  script-only reads, output-to-file capture, scratch location, v01/v02);
  reasoning-model support (sentinel opt-in via NORA_LLM_REASONING_SENTINEL +
  tolerant first-balanced-JSON fallback) — fixed all "Failed to parse" warnings
  on the proprietary endpoint.
- Resilient taxonomy extraction (534a401): per-unit fail-soft (LLM error /
  unparseable response marks the unit failed, run continues, WARN + TAX-W004);
  resumable `out/taxonomy/extraction_state.json` ledger written after every
  unit (survives hard kills); failed units retry automatically on re-run —
  re-running the same command IS the retry mechanism; fingerprint stamped only
  on zero-failure runs; `LLMParseError` replaces silent empty-success;
  stale features-file cleanup.
- Multi-release semantics made explicit (534a401, user decisions): newest
  release wins per (MNO, plan_id); MMMYYYY release names parsed to YYYYMM
  (user correction — Jul2026 sorts before Mar2025 alphabetically); superseded
  copies cost no LLM calls; fingerprint hashes the selected set only.
- Plan-unit extraction (b8a93c8, user's mno-b finding): chapter-per-plan docs
  (one doc, empty tree plan_id, 87 plans) previously produced one empty-prefix
  `_features.json` + one whole-doc call truncated at the 200-line TOC cap.
  `split_tree_by_plan` + unit-level newest-release supersession + per-plan
  ledger keys `<path>#<plan>` (independent fail/resume/retry per chapter-plan).
- Heading inheritance (a2f6fd6): two paste-safe diagnostics on the real tree
  showed the 870 dropped nodes were heading nodes (leaves carry parent_section
  but no section_number). Headings now join the majority plan of the leaves
  they enclose (ancestry join, immune to chapter-boundary misattribution).
  Work-PC verified: drops 870 → 115, residue confirmed as requirement-free
  tail sections (references/prose-without-req-id). plan_name is now the
  chapter title.
- Flaky-test root cause (a2f6fd6): test_query MockEmbedder used salted builtin
  hash() — glossary-pin ranking test was a per-process coin flip; now md5.
- Work-PC state: taxonomy complete for all 3 MNOs (mno-b = 87 per-plan files);
  ledger-cleanup one-liner used to invalidate heading-less mno-b extractions.

### In progress
- (nothing mid-flight in the repo — taxonomy stage settled; next work is the
  user-run Phase 5 pilot)

### Next
- Phase 5 pilot: smallest cell, `--run-name enrich-pe-v1 --only <cell>`,
  verify `{taxonomy_block}` resolution in prompt.txt (mno-b plans now
  resolvable), batches.jsonl shapes, failure histogram → fan out → Phase 6
  promote `--sira-build` → serve.
- Eval loop as a separate strand (carried).

### Flags
- Taxonomy last-write-wins across releases is RESOLVED (newest-release-per-plan
  selection); the earlier per-cell-layout idea is superseded — no longer needed.
- Un-req-id'd prose sections in chapter-per-plan docs are invisible to
  taxonomy/enrichment by design (no req_id to key on); content remains
  available via section nodes / build_context_string. Accepted 2026-07-27.
- Batched enrichment path still unit-tested only; Phase 5 must start with one
  small cell (carried).
- Per-MNO query/relevance prompt selection in the query-time service still
  deferred (carried).

## 2026-07-29 — Power-outage recovery: heal-torn + lane repair flags; MNO-C batch-size root cause

### Done this session
- `8593317` single operational doc: `docs/runbook-fresh-env.md` merged into
  `docker/README.md` (phase-ordered ingest cycle, generalized `pre-<cycle>` /
  `<run-name>` placeholders) + new "Bring up from a published release"
  section (pull.sh → IMAGE_PREFIX/IMAGE_TAG → `up -d` WITHOUT `--build`).
  `053064e` repointed the STATUS flag (architect edit).
- `240742d` heal-torn recovery (D-DRAFT-9): `sira_incremental heal-torn`
  (torn-line drop + two-way kept↔enrichment invariant repair; trace.failed
  untouched) + `sira_lane --heal-torn / --retry-failed
  [--include-all-filtered]`, heal-before-retry, skipped with a note under
  `--wipe-all-derived`. 14 new tests (55 passing); docs in docker/README +
  sandbox/README §2.8/command-ref + SETUP crash gotcha.
- Root-caused the MNO-C enrichment failure wave from paste-safe counts:
  parse_error batches average 98.4 reqs vs 13.2 for ok → jumbo per-plan
  batches (packer allowed 155 reqs / 14k-token responses) truncate at the
  endpoint's REAL output ceiling → "no JSON object" → 3 identical requeue
  rounds → 1,666 missing_in_batch_response (2,456 kept, all recoverable).
- Ops: JOB_UID ownership diagnosis for `/data/env/reports` permission-denied
  (+ `&&` short-circuit warning); sentinel ruled OUT for the batch path
  (`_b_llm` bypasses the NORA provider; `parse_batch_response` is
  prose-tolerant — fences + first-{...last-} scan).

### In progress
- Work-PC round-2 fan-out running: `NORA_SIRA_BATCH_RESP_TOKENS_PER_REQ=400`
  (max 35 reqs/batch), image rebuilt at `240742d`, relaunched with
  `--heal-torn --retry-failed`; verification pending (reqs ≤ 35,
  parse_error ≈ 0, rounds decay, failed residue only all_filtered/no_phrases).

### Next
- Read round-2 verification counts; if MNO-C is clean, finish fan-out →
  Phase 6 promote `--sira-build` + pin `NORA_SIRA_DOC_ENRICH_RUN=enrich-pe-v1`
  in `.env.sira-query` → recreate the serve stack.
- Quality follow-up: per-MNO doc prompt tightening ("return ONLY the listed
  req_ids") to cut unknown-id response waste — prompt commit (internal
  remote) + rebake, then optional targeted re-enrich.
- Eval loop as a separate strand (carried).

### Flags
- Batch sizing lesson: `resp_tokens_per_req` must reflect the endpoint's
  REAL output ceiling, not the 64k−50k reserve split — the 90/req default
  permitted 155-req batches whose responses truncated to prose. Work-PC value
  now 400; consider a sizing comment in `env.sira-batch.example` once round-2
  confirms the value.
- 5 taxonomy-refused plans enrich without taxonomy blocks (fail-soft,
  log-verified); prompt-framing recovery stays optional/deferred.
- Batched path is now real-LLM-exposed at scale — the "unit-tested only"
  flag can drop once round-2 verification passes.

## 2026-07-29 (evening) — Reasoning sentinel for batched enrichment; unknown-req_id warnings root-caused cosmetic

### Done this session
- `03c3d6e` batch-path reasoning sentinel (D-DRAFT-10):
  `NORA_SIRA_BATCH_REASONING_SENTINEL=1` code-appends the
  `===FINAL_ANSWER===` instruction in `compose_prompt` (per-batch — no
  per-MNO prompt-file edits, header token estimate includes it);
  `parse_batch_response` keeps only post-marker text. Plus parser
  hardening for tagged/fenced reasoning: `<think>` spans stripped, fenced
  JSON candidates tried LAST-first so a thinking-draft never shadows the
  final answer (pre-fix, a fenced draft could silently win — real bug
  exposed by the debug prints).
- `4f1d853`..`c743339` DEBUG_RAW observability suite, iterated live
  against the work-PC run: response head+tail, marker PRESENT/ABSENT,
  plan name, requested-vs-unknown ids side by side (first 20). Opt-in;
  outputs carry corpus content — local-only, redact before sharing.
- Debugging arc CLOSED on the work-PC round-2 fan-out:
  - sentinel honored (`marker PRESENT`), `missing ≈ 0`, `failed {}`;
  - unknown-req_id warnings root-caused COSMETIC — batches carrying
    coarse-granularity rows (`doc:<plan>`, `section:<…>`) embed real
    req_ids in their text and the model itemizes them; strict discard is
    correct (each req enriches in its own batch, no double-writes);
  - granularity check: kept `{req: 3474, doc: 47, section: 314}`,
    failed `{}` — all three levels enriching;
  - `RESP_TOKENS_PER_REQ=400` validated in production (≤35-req batches,
    parse_error ≈ 0).

### In progress
- Work-PC fan-out running healthy across remaining cells (sentinel +
  400/req knobs live; env knobs apply per `compose run`).

### Next
- On fan-out completion: per-cell `trace.failed` histograms (accept only
  `all_filtered`/`no_phrases` residue), `grep "No taxonomy for plan"` =
  exactly the 5 refused plans → Phase 6: `promote.sh --sira-build`, pin
  `NORA_SIRA_DOC_ENRICH_RUN=enrich-pe-v1` in `.env.sira-query`, recreate
  serve stack, `/healthz`.
- Optional token-cost follow-up (NOT correctness): "EXACTLY one key per
  listed req_id" instruction next to the sentinel — coarse-granularity
  rows trigger the itemization waste.
- Eval loop as a separate strand (carried).

### Flags
- Cells enriched BEFORE the sentinel/parse-order fix may hold
  draft-quality phrases (pre-fix, a fenced thinking-draft could win the
  parse); only revisit via targeted re-enrich if eval shows weak cells.
- The "batched path unit-tested only" flag is now DROPPED — real-LLM
  exposure at scale verified this session.

## 2026-08-03 — Single-req mode, three-layer verify, coarse-chunk skip policy, permanent-refusal fallback

### Done this session
- `62b793c` `NORA_SIRA_BATCH_MAX_REQS` + `sira_lane --max-reqs N`:
  explicit reqs/batch cap (only ever tightens the response-budget-derived
  cap); `1` = single-req mode — same batched prompt (taxonomy block
  included), packing/retry/trace machinery, one LLM call per req. The
  standard retry pairing: `--retry-failed --max-reqs 1`.
- `84efc1e` + `8cd55ea` three-layer verify architecture:
  `sira_incremental verify-run` (per-cell primitive; batches/trace/
  coverage/invariant blocks, FAIL=structural / WARN=quality verdict,
  paste-safe counts only), `sira_multi --verify` (cell sweep instead of
  build; `--only` intersection, `--compare-run` Jaccard A/B), `sira_lane
  --verify` (post-build gate). Cell discovery unified on
  `sira_cells.enumerate_cells`. Separate triage layer:
  `sira_enrich_inspect --failed` (LOCAL-ONLY id-bearing listing,
  status → plan grouping).
- `6436711` verify-run scopes batch stats to the LATEST invocation
  (batches files are append-only across resumes; segmentation on
  batch-id sequence resets with concurrency-jitter tolerance, `history:`
  line for excluded eras) + sanitized top-5 error histogram
  (URL/endpoint redaction, 80-char cap). A clean retry pass now PASSes
  on its own merits.
- `abe5154` coarse-chunk policy: `doc:`/`section:` corpus rows SKIPPED
  from enrichment by default, traced as `skipped_doc_chunk` /
  `skipped_section_chunk` (benign, coverage-counted). Opt-ins
  `--enrich-doc-chunks` / `--enrich-section-chunks` (lane→multi→env);
  `retry-failed --include-skipped` evicts them back into scope. Verify
  splits failed/non-benign/uncovered by row type. DEBUG_RAW widened to
  dump on ALL parse anomalies (no-JSON, ids-absent, unknown-ids) — the
  first two were previously invisible.
- `baa3f14` permanent-refusal fallback: `sandbox/llm_refusal.py`
  detector (marker-prefix + no-JSON-payload; markers env-local via
  `NORA_LLM_REFUSAL_MARKERS`, never committed). Batch lane: refused
  call → one fallback-LLM shot (same prompt/budget, batch row
  `llm=fallback`, verify shows `fallback-answered N`); markers set but
  no fallback → fail fast as `llm_refused` (no requeue burn), retryable
  via `--include-refused` once a fallback exists. Query service: same
  detection inside `_llm_call` (covers query-enrich + chat rerank),
  `/healthz` reports `refusal_fallback {configured, used}`. Taxonomy
  lane deferred (TBD).
- Public-history scrub executed (dir-name string in a docstring):
  two filter-repo passes + force-push; 63 doc SHA references re-pointed
  from the CUMULATIVE commit-map (`7dbbebe`); backup ref lifecycle
  managed and deleted after work-PC re-sync. `~/work/utils`
  `adopt-github-rewrite.sh` upgraded to an exact-split
  `rebase --onto` via the recorded last-synced tip (conflict-free for
  content scrubs; patch-id fallback retained) — validated by sandbox
  simulation; work PC shepherded through two failed recoveries to a
  clean adopt + sync.
- Debugging arc CLOSED on run `enrich-pe-v1` (4 cells): the ~2,100
  persistent solo-call failures decomposed via the new by-type verify
  into 93% coarse doc/section chunks (model returns `{}`/no JSON for
  rollup texts — now policy-skipped) + a 146-req residue the endpoint
  PERMANENTLY refuses (deterministic non-answer, DEBUG_RAW-confirmed;
  111 of 146 concentrated in one cell). Post-policy sweep: all four
  cells' non-benign = req-only 146; coverage 100%, invariants clean.

### In progress
- Work-PC: configure refusal markers + fallback endpoints in
  `.env.sira-batch` / `.env.sira-query`, rebake `sira-batch` +
  `sira-query`, then `--retry-failed --max-reqs 1 --verify` to route
  the 146 through the fallback (expect `fallback-answered` counts,
  non-benign → ~0).

### Next
- If the fallback retry pass is clean → Phase 6: `promote.sh
  --sira-build`, pin `NORA_SIRA_DOC_ENRICH_RUN=enrich-pe-v1`, recreate
  serve stack, `/healthz` (incl. `refusal_fallback.configured`).
- Eval loop as a separate strand (carried).

### Flags
- The run is serviceable even before the fallback pass: 24,957 kept
  rows, 100% coverage — the 146 unenriched reqs still index on raw
  text; promote is not blocked on the fallback retry.
- Fallback-model enrichments mix into the same run, recoverable via the
  `llm=fallback` batch-row tag if quality auditing is ever needed.
- Pre-sentinel draft-quality-phrase flag (2026-07-29) still open —
  only revisit via targeted re-enrich if eval shows weak cells.

## 2026-08-04 — Fallback retry pass verified: 146/146 recovered; v2 serve stack live

### Done this session
- verify-5 vs verify-4: the fallback retry pass recovered ALL 146
  permanently-refused reqs (kept-req deltas +111 / +5 / +5 / +25 across
  the four cells; every answering batch tagged `llm=fallback`).
  Non-benign failures 0 in all cells, coverage 100%, invariants clean —
  run `enrich-pe-v1` declared promote-ready.
- Residual WARNs triaged as non-blocking: 5 parse_error batches in the
  big cell were fallback outputs recovered by requeue
  (missing@final-round 0); the two small-cell WARNs are stale-era
  bleed-through — a retry scope smaller than the 64-batch jitter
  tolerance merges the pre-fallback era into the "latest invocation"
  (batch-id reset drop ~14 < 64). Trace layer is authoritative and
  clean; artifact is cosmetic.
- Phase 6 executed on the work PC: `promote.sh` passed for BOTH
  `--nora-build` and `--sira-build`; v2 serve stack brought up on a
  separate port combo per the documented A/B-stacks pattern (own
  wiring env, per-stack web-state + service env files, shared
  feedback/corrections; `NORA_SIRA_QUERY_URLS` fan-out set so one
  enrichment-review Apply reloads both stacks' sira-query).
- v2 verification: /healthz on both stacks and test-page query smoke
  confirmed good.

### In progress
- Apply fan-out check (enrichment-review Apply reloading BOTH stacks'
  sira-query via NORA_SIRA_QUERY_URLS) — verify on first real
  corrections use; not blocking.

### Next
- Strand is land-ripe: 14 pending drafts + this arc closed —
  `/land-strand sira-enrichment-pe` when ready.
- Eval loop as a separate strand (carried).

### Flags
- Verify latest-invocation splitter merges eras when the retry scope
  is < ~64 batches (jitter tolerance) — small cells can inherit stale
  WARNs. Cosmetic (trace verdict is truthful); candidate fix: delimit
  invocations with a marker row instead of the batch-id-reset
  heuristic.
- Pre-sentinel draft-quality-phrase flag (2026-07-29) still open —
  revisit only via targeted re-enrich if eval shows weak cells.
