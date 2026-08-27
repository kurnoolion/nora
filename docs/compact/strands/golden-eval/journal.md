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

## 2026-08-07 — Pipeline refusal coverage + taxonomy refusal diagnosis

### Done this session
- Closed the last refusal-coverage gap (95e6538): the pipeline had none —
  taxonomy and eval construct providers via
  PipelineContext.create_llm_provider, bypassing both existing wrap sites
  (web builder, golden_cli). Wrap moved INSIDE create_llm_provider
  (construction split to _construct_llm_provider), so every stage plus the
  debug/miner CLIs and web's builder inherit it from one place;
  maybe_wrap_with_refusal_fallback made idempotent and web's now-redundant
  wrap dropped. golden_cli keeps its own (builds providers directly).
  NORA_LLM_FALLBACK_* block added to env.nora-pipeline.example; invariants
  added to pipeline + llm MODULE.md ("constructing a provider outside
  create_llm_provider opts out of refusal coverage"). 3 new tests; full
  suite 1646 passed. D-DRAFT-7 amended (6a7748a).
- Diagnosed a live work-PC failure: TAX-E001 "unparseable LLM response"
  across many plans during a new mno-a release ingest. Confirmed via
  tax_debug as permanent refusals — the exact class 95e6538 fixes.
  Two observations explained against the code: taxonomy covering mno-b /
  mno-c is by design (global union stage; --mno/--release scope only the
  per-cell stages), and the repeated retries are the documented
  failed-unit retry path (per-unit skip requires status == "ok", so failed
  units re-attempt on every run — user's reading, code-confirmed).
- Config questions answered from code, no changes needed: ingest scoping
  (--mno / --release, comma-separated, per-cell stages only);
  NORA_LLM_BASE_URL keeps /v1 for openai-compatible, drops it for ollama
  (NORA_LLM_FALLBACK_BASE_URL always includes /v1 — always
  OpenAI-compatible).

### In progress
- Work-PC deploy of 95e6538: fill NORA_LLM_FALLBACK_* in
  .env.nora-pipeline, rebuild the pipeline image, re-run the same ingest
  command (failed units retry automatically).

### Next
- Verify the taxonomy failed set clears and the corpus fingerprint stamps
  on a zero-failure run — that also restores the plans currently missing
  from taxonomy.json.
- Golden eval (unchanged): expert authoring → first Stage-1 batch A/B →
  Stage-2 end-to-end; confirm v1 /sira-query row fields; GEV- vs EVL-
  prefix call before land.

### Flags
- Taxonomy output is currently DEGRADED on the work PC: a refused plan's
  <plan>_features.json is deleted and excluded from the consolidated
  taxonomy.json (which is still written), so refused plans have been
  silently absent for however many runs this recurred. The stage returns
  WARN and unlinks the fingerprint so it can never cache-lock — a clean
  run restores the full set. Anything built from taxonomy.json meanwhile
  (graph, vectorstore) may carry the thinner feature set.
- Extraction max_tokens is a hardcoded 4096 in taxonomy/extractor.py — not
  the cause here, but the candidate if truncation-shaped parse failures
  ever appear (reasoning models can spend the budget before closing JSON).

## 2026-08-09 — mno-a enrichment plan-stamp fix (taxonomy blocks never applied)

### Done this session
- Work-PC verification closed the 95e6538 loop: taxonomy failed set
  cleared after the fallback deploy; corpus restored.
- The next SIRA enrichment build surfaced 42 "No taxonomy for plan
  'Reqs-<PLAN>'" warnings on mno-a — root-caused to a plan-key mismatch:
  taxonomy files are keyed <plan_id>_features.json, but heading-mode
  requirement rows in the adapter stamped **plan**: with plan_name
  (a pre-taxonomy-blocks back-compat leftover from the D-DRAFT-1 per-req
  plan work). plan_of() fed the name to load_taxonomy_block() → every
  lookup missed → the whole mno-a enrichment build ran WITHOUT taxonomy
  context in its prompts, silently.
- Fixed (36b08f7): requirement rows now stamp the bare plan_id in both
  detection modes; plan_name stays on the composite doc/section rows.
  Composite req-row stamps deliberately rejected — the sira-query plan
  dropdown lists req-row stamps verbatim and _plan_matches documents the
  single-value invariant. 3 new tests pin the stamp value (previously
  unasserted). Suites: adapter 49, sandbox 384, core 1646/109 skipped.
- Deploy guidance: no NORA stages (adapter inputs unchanged); SIRA full
  re-enrich of mno-a cells via sira_lane --wipe-all-derived --only
  <cells> (prune evicts nothing without a committed baseline; 100% of
  rows changed anyway). Leading-id cells unaffected.

### In progress
- Work-PC re-enrich of the mno-a cells with taxonomy blocks now in
  prompts: pull + rebuild sira-batch image, run the lane with
  --wipe-all-derived --only, restart sira-query, confirm zero
  "No taxonomy" warnings and larger prompt_tokens_est.

### Next
- Optionally `sira_incremental commit --full` per rebuilt cell so future
  incremental prune flows have a hash baseline.
- Golden eval (unchanged): expert authoring → first Stage-1 batch A/B →
  Stage-2 end-to-end; confirm v1 /sira-query row fields; GEV- vs EVL-
  prefix call before land.

### Flags
- Any pre-fix enrichment build of a heading-mode corpus was enriched
  without taxonomy context — if other heading-mode cells exist beyond
  mno-a, they carry the same silent degradation until re-enriched.

## 2026-08-10 — enrich-timeout triage; reverse-proxy support; variant-lineage methodology

### Done this session
- mno-a re-enrich verify (work PC) confirmed the plan-stamp fix: full
  coverage, zero "No taxonomy" warnings, no refusals. New failure mode:
  548 reqs / 48 batches all "Timeout on reading data from socket" —
  taxonomy-block prompts are larger/slower and trip the 300s default
  NORA_SIRA_ENRICH_LLM_TIMEOUT. Guidance: timeout 900, --retry-failed on
  the same run name (no wipe; kept enrichments stay cached), --max-reqs 1
  single-req mode for the retry.
- Reverse-proxy support shipped (2bef80c): web module audited — already
  root_path-clean by design except four unprefixed redirects (team-gate
  middleware + three /admin-unlock), now prefixed via
  scope["root_path"]; new NORA_WEB_ROOT_PATH env var (env > baked
  web.json, normalized) as the per-deployment prefix knob. Caddy
  handle_path + flush_interval -1 recipe in docker/README. 8 new tests;
  suite 1654/109. Committed artifacts framed purely as reverse-proxy
  support (no org context), per user instruction.
- Variant-lineage methodology documented (4476d3f, 8fd815a):
  docker/README "Variant lineages — comparing ideas, not code" — variant
  = recipe (prompt set + knobs + wiring env), one code line/image set,
  per-variant build+serve lineages over the shared raw corpus, pooled
  GOLDEN_DIR/FEEDBACK_DIR for same-corpus eval. Prompt delivery
  discovered to be runtime-resolved → mounted <build-env>/prompts/
  documented as primary (Phase 3 "publish prompts"); baked path legacy.

### In progress
- Work-PC: mno-a retry running (--retry-failed --max-reqs 1, timeout
  raised) — verify log pending; expect failed set to collapse to ~42
  benign rows, then restart sira-query + optional commit --full baseline.
- Work-PC rollout of reverse-proxy serving: rebuild web+sira-query pairs
  for both stacks from main, NORA_WEB_ROOT_PATH=/nora1|/nora2, Caddy
  handle_path blocks.

### Next
- Materialize the variant recipes: nora-builds/<variant>/prompts/ +
  RECIPE.md per build env; per-variant wiring envs so a new release
  ingests into nora1 (generic recipe) and nora2 (PE recipe) from the
  same raw corpus.
- Golden eval (unchanged): expert authoring → first Stage-1 batch A/B →
  Stage-2 end-to-end; confirm v1 /sira-query row fields; GEV- vs EVL-
  prefix call before land.

### Flags
- Enrichment timeout knob is deployment-local: 900s + --max-reqs 1 were
  chosen for THIS endpoint's latency; revisit if batch sizes grow or the
  endpoint changes.

## 2026-08-11 — rollout day: retry verified; root_path semantics fix; promote serve-set

### Done this session
- mno-a retry verify came back clean (--retry-failed --max-reqs 1,
  timeout 900): zero timeouts (was 548), missing@final-round 0, coverage
  full, failed set entirely benign (24 req-level: 23 all_filtered +
  1 no_phrases, plus intentional doc/section skips). 315/567 answers via
  the refusal fallback — fallback-model quality is load-bearing for this
  corpus slice. commit --full baseline recorded for the cell.
- Serving rollout: new cell was invisible to sira-query — serve stack
  mounted a label promoted BEFORE the build. Fresh label + repoint +
  recreate fixed it; healthz doc_enrich_source verified against the
  pinned run.
- Reverse-proxy deploy surfaced that the shipped strip-prefix contract
  was wrong (42174a7): Starlette 1.0 expects the ASGI path to INCLUDE
  root_path (routing strips it) — a stripping proxy 404s the /static
  mount and loops the team gate (raw-path allowlist compare). Fix: the
  proxy passes the prefix through (Caddy `handle` + matcher, not
  `handle_path`); middleware _route_path() strips scope root_path before
  the gate allowlist and metrics label; docs/env example/MODULE.md
  invariant corrected; prefixed-static regression test added. All
  surfaces verified through the proxy: statics, gate, admin-unlock, SSE,
  JS views. Side effect: direct-port browsing works WITH the prefix in
  the URL.
- Eval Studio picker served empty off the promoted label (3757c22): the
  picker + req-browser read out/parse/ trees at SERVE time, but
  promote.sh's SERVE-set predated those surfaces — parse was opt-in
  (--include-parse). parse now in the default serve-set (flag kept as
  no-op); re-promote verified, picker fully populated.

### In progress
- nora1 stack: not yet proxied — needs NORA_WEB_ROOT_PATH=/nora1 in its
  web env, its Caddy handler, rebuild + recreate (mechanical repeat of
  nora2).

### Next
- Materialize the variant recipes: nora-builds/<variant>/prompts/ +
  RECIPE.md per build env; per-variant wiring envs.
- Golden eval (unchanged): expert authoring → first Stage-1 batch A/B →
  Stage-2 end-to-end; confirm v1 /sira-query row fields; GEV- vs EVL-
  prefix call before land.

### Flags
- D-DRAFT-10 records the now-reversed strip-prefix proxy contract —
  amended by D-DRAFT-12 this session; at /land-strand promote them
  together (or fold 12 into 10) so the canonical log never teaches the
  broken convention.
- Labels promoted before 3757c22 lack out/parse — any stack repointed at
  an old label serves an empty Eval Studio picker; re-promote rather
  than reuse.

## 2026-08-19 — sweep forensics, snapshot discipline exercised, relaunch

### Done this session
- Reconstructed the stalled 12-cell sweep's true state purely from run
  artifacts (StackStamp `id:` lines): the code-version boundary separated
  pre-rebuild rounds from campaign runs; eval-set digest drift
  (38@b56d6a31 → 39@1ce270b2) proved the sweep had been running against
  the LIVE golden set — the snapshot-freeze step had never actually
  happened; knob digests decoded the 2-stacks × 2-arms grid
  (launch-spacing corroborated: ~8 min ≈ enrichment ON, seconds ≈ OFF).
  Net: only ~1 of 12 cells existed on the final build + set.
- Froze a true snapshot (66 sample files; the live set grew 39→66 via
  Eval Studio additions mid-week) and re-pointed eval runs at it via the
  golden-dir mount override — studio contributions now ride the next
  campaign by construction, with no freeze imposed on contributors.
- Diagnosed and fixed a stale eval-CLI image on the second stack
  (pre-eval-module bake; "No module named …golden_cli" = the runbook's
  stale-image signature): rebuilt under the serving env file so the
  build-time and run-time image names match; verified both stacks serve
  code_version 3359a74.
- Relaunched: detached script, 2 stacks × 2 arms × 3 repeats, labels
  v{1,2}-{on,off}-r{N}; ON block (6 cells) running.

### In progress
- ON block executing on the field machine; OFF block follows a manual
  arm flip (env edit + `up -d` + healthz knob verification per stack).

### Next
- Verify cell 1's identity line (`set=N@digest` constant thereafter,
  knobs digest per arm) and wall-clock → real sweep ETA (the 66-sample
  set will exceed the old 55 min/cell estimate).
- On grid completion: first Stage-2 floors (per stack, 3 repeats) and
  the enrichment verdict; judge-loss check (GEV-E004 well below 15%).

### Flags
- All pre-relaunch campaign runs (set=38 group and the single set=39
  run) are incomparable with the new sweep — excluded by digest, no
  cleanup needed.
- Mid-campaign eval-set growth was legitimate teammate activity, not
  error; the snapshot procedure is now exercised, not just ruled.

## 2026-08-27 — run-inspection tooling + operator runbook

### Done this session
- Verified the first ON-block sweep cell (v2-on-r1) against the campaign
  anchors: identity line repeats the frozen-set digest verbatim, the
  serving code sha, and the ON-arm knobs digest — the baseline every
  remaining cell must match.
- golden_report_cli (2a1a897): read-only per-sample inspector over a
  run's report.json — expected vs retrieved req_ids with ranks,
  `--misses` triage filter, `--samples-dir` override; 5 tests;
  MODULE.md public-surface entry. Deliberately stdlib-only and
  single-file so a field machine runs it with bare Python 3 — no repo
  checkout, no container.
- docs/golden-eval-guide.md (04e7273): operator runbook — running an
  eval, decoding the GEV block (stamp fields, GEV codes), inspecting a
  run dir, and the NFR-8 sharing boundary; pointer added in
  core/src/eval/MODULE.md.
- Guide corrected to deployment reality (f1c4645): dockerized
  nora-pipeline one-shot invocation now leads, with the field-learned
  gotchas (GOLDEN_DIR mount trap, query-service-not-web-app target,
  image rebuild rule); documented that --stack-url / --stack-label /
  --env-name are explicit CLI args, nothing env-file-inherited — the
  decoupling that makes same-env release A/B possible.

### In progress
- ON block (6 cells) running detached on the field machine; OFF block
  awaits the manual arm flip (env edit + `up -d` + healthz knob
  verification per stack).

### Next
- Verify each pasted GEV block against the campaign anchors (set digest
  verbatim; knobs digest per arm).
- After the ON block: arm flip, then the detached OFF block.
- On grid completion: first Stage-2 floors (per stack, 3 repeats) +
  enrichment verdict; judge-loss check (GEV-E004 well below 15%).

### Flags
- Strand paused 2026-08-27 — session rebinds to `team-operations`.
  Sweep monitoring continues reactively; `/land-strand` still queued
  (16 drafts; D-DRAFT-10 + D-DRAFT-12 promote together).
