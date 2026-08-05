## 2026-08-05 — Architecture + full implementation: schema, runners, GEV triple, Eval Studio

### Done this session
- Architecture round (commit 6e4d755): eval + web MODULE.md contracts for the
  golden eval set; D-DRAFT-1..5 drafted (per-sample JSON + GEV triple; black-box
  Stage-1 per stack; pinned_chunk_ids reuse; versioned LLM judge; studio as web
  router + shared req-tree helper). Open-source alternatives evaluated and
  rejected on record (Label Studio/Argilla in D-DRAFT-5, judge frameworks in
  D-DRAFT-4; statement-decomposition prompt pattern borrowed). FR-38/FR-39
  added to requirements.md; GEV compact-report/QC/FIX formats designed into
  STRAND.md Notes.
- Implementation round (commit c9520c1): eval/golden.py (GoldenSample schema,
  atomic per-sample JSON under <env_dir>/eval/golden/), eval/golden_runner.py
  (Stage-1 recall via POST /sira-query with qualified matching + recall@k;
  Stage-2 pins ALL Stage-1 retrieved req_ids into QueryPipeline and judges vs
  golden_response; GoldenRunReport + GEV compact block + format_ab_delta;
  run_all batch with per-sample error capture), prompts/judge_v1.txt,
  golden_cli.py (mirrors web pipeline construction; degrades to Stage-1-only).
  GEV codes + golden QC/FIX templates registered in pipeline. Web: shared
  req_tree.py (per-cell aware; req_browser delegated), routes/golden_eval.py +
  eval_studio templates (board, MNO→Plan→Release picker with latest-release
  default and full qualification, direct-paste auto-qualify, Stage-1 preview
  with retrieval-assisted seeding + bias caution, curation chat over
  parse-tree texts, golden save), team-mode whitelist + nav.
- 70 new tests across 5 files; full suite 1622 passed / 109 skipped.

### In progress
- Work-PC verification of c9520c1: picker cascade vs real parse layout,
  v1-stack result rows (mno/release fields present?), curation-chat quality
  with the on-prem model, first real golden samples.

### Next
- Expert onboarding: first ~5 samples through the studio, then batch Stage-1
  against both stacks (v1/v2) and eyeball the GEV blocks + A/B delta.
- Stage-2 end-to-end on the work PC (needs NORA store + graph + real LLM).
- Decide GEV- vs folding under EVL- before land (D-DRAFT-1 records GEV-).

### Flags
- v1 gaps (deliberate): golden_cli emits no metrics-DB KPIs (headless; prints +
  run dir only); no run_golden pipeline stage (standalone build step per spec).
- MODULE.md STRUCTURE blocks stale for eval/web/pipeline until regen-map
  (run this close-session, step 5).
