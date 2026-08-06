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

## 2026-08-06 — Work-PC verification round: deployment, studio hardening, perf fix, refusal fallback

### Done this session
- Work-PC bring-up of the Eval Studio (11 commits, 9feb883..1aba6d3, all
  verified on the work PC): stale-image and env-file-mismatch deployment
  issues diagnosed (build/up must share one env file; serve vs build wiring
  warning added to env.example); serve labels backfilled with out/parse via
  promote.sh's hardlink pattern — --include-parse is now standard for any
  label a web stack serves.
- GOLDEN_DIR pooled compose mount (9feb883): samples + runs shadow
  <env>/eval/golden on nora-web AND nora-pipeline, so the eval set lives
  outside promoted serve labels (survives label GC, shared across A/B
  stacks). Defaults to <NORA_ENV_DIR>/eval/golden — single-build setups
  unchanged.
- Studio features from first real expert use: draft delete (expert-allowed,
  UI-confirmed; promoted samples admin-only), select-all + bulk add in the
  picker (respects filter; gt/add-bulk endpoint), chat send spinner +
  hx-disabled-elt, query-aware curation system prompt (query + answering
  guidance embedded), system prompt shown read-only in the chat card,
  first Send needs no text (kickoff draft; empty refinements warn).
- Performance fix (2973403): editor renders scanned every parse tree per GT
  entry ON the event loop — minutes-long renders froze the whole app. Now:
  mtime+size-keyed tree cache in req_tree, cell-scoped find_req (qualified
  entries scan one cell), all studio handlers sync def (threadpool). Verified
  fast on the real corpus, both stacks.
- Cross-strand fix (sira-enrichment-pe lineage; aad1ddf + 1aba6d3): permanent
  refusals in the query path surfaced as user-visible answers. Two layers:
  .env.sira-query fallback was never configured (config fix, healthz-verified),
  and the synthesis leg had NO fallback — new core/src/llm/refusal.py
  (detection twin of sandbox/llm_refusal.py + RefusalFallbackProvider),
  wrapped at both LLM-builder choke points (web query/chat lane, golden_cli).
  'Synthesized by <model>' epilogue on both synthesis modes (LLMSynthesizer
  + select-synth lane) names the model that actually answered — visible
  fallback provenance. Team-verified.
- Suite grew 70 → 92 golden/studio/refusal tests; full suite 1643 passed.

### In progress
- Expert authoring: studio fully usable on both stacks; first real samples
  being created.

### Next
- First ~5 samples → batch Stage-1 against both stacks, eyeball GEV blocks +
  A/B delta.
- Stage-2 end-to-end on the work PC (labels lack out/vectorstore + out/graph —
  point --vectorstore-dir/--graph at the build env or backfill into labels).
- Confirm whether v1's /sira-query rows carry mno/release (visible in any
  Stage-1 preview against v1).
- Decide GEV- vs folding under EVL- before land (D-DRAFT-1 records GEV-).

### Flags
- Stage-2 candidates now carry the 'Synthesized by <model>' epilogue; golden
  responses don't. judge_v1 says style must not affect score — if real runs
  show the judge citing it, strip the epilogue in run_stage2 before judging.
- v1 gaps carried: no metrics-DB KPIs from golden_cli; no run_golden stage.
- Refusal-fallback coverage now complete EXCEPT the taxonomy lane (deferred
  since baa3f14).
- The refusal-fallback work belongs to the landed sira-enrichment-pe lineage
  but is journaled/drafted here (session was bound to golden-eval) — flag
  its D-DRAFT for provenance at promotion time.
- STRUCTURE blocks stale for llm (refusal.py, answering_model) and web
  (req_tree scoping, gt_add_bulk); MAP file tree lacks the two new files.
  No structural trigger fired (no module/edge changes) — regen at next
  triggered regen-map run.
