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
- Slice 1: sizing scanner (sandbox/scan_enrichment_stats.py, fa428d7).
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
- Slice 5, NORA half (commit 9e8c036): `FeatureExtractor(llm, overview_dir=)`
  + `resolve_corpus_overview()` (highest-version glob); `{corpus_context}`
  section in the extraction prompt — byte-identical when absent (test-locked);
  `TAX-W003` registered; `run_taxonomy` reads `NORA_TAXONOMY_OVERVIEW_DIR`;
  cache-fingerprint decision resolved: overview files hashed into the corpus
  fingerprint (auto-bust, no `--force`); `taxonomy_cli --overview-dir` parity;
  taxonomy + pipeline MODULE.md updated; 6 new tests, suite 1541 green.
- Prompt-location decision (user, commit 561a5a6): per-MNO prompt artifacts
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
- Resilient taxonomy extraction (344e095): per-unit fail-soft (LLM error /
  unparseable response marks the unit failed, run continues, WARN + TAX-W004);
  resumable `out/taxonomy/extraction_state.json` ledger written after every
  unit (survives hard kills); failed units retry automatically on re-run —
  re-running the same command IS the retry mechanism; fingerprint stamped only
  on zero-failure runs; `LLMParseError` replaces silent empty-success;
  stale features-file cleanup.
- Multi-release semantics made explicit (344e095, user decisions): newest
  release wins per (MNO, plan_id); MMMYYYY release names parsed to YYYYMM
  (user correction — Jul2026 sorts before Mar2025 alphabetically); superseded
  copies cost no LLM calls; fingerprint hashes the selected set only.
- Plan-unit extraction (8a30095, user's mno-b finding): chapter-per-plan docs
  (one doc, empty tree plan_id, 87 plans) previously produced one empty-prefix
  `_features.json` + one whole-doc call truncated at the 200-line TOC cap.
  `split_tree_by_plan` + unit-level newest-release supersession + per-plan
  ledger keys `<path>#<plan>` (independent fail/resume/retry per chapter-plan).
- Heading inheritance (4a7b8e6): two paste-safe diagnostics on the real tree
  showed the 870 dropped nodes were heading nodes (leaves carry parent_section
  but no section_number). Headings now join the majority plan of the leaves
  they enclose (ancestry join, immune to chapter-boundary misattribution).
  Work-PC verified: drops 870 → 115, residue confirmed as requirement-free
  tail sections (references/prose-without-req-id). plan_name is now the
  chapter title.
- Flaky-test root cause (4a7b8e6): test_query MockEmbedder used salted builtin
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
