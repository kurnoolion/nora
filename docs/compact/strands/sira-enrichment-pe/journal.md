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
