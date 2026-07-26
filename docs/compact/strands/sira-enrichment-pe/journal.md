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
