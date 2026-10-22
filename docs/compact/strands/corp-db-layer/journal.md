## 2026-10-08 — Strand opened; artifact inventory for the corporate DB layer

### Done this session
- Strand created and bound; phase set to architecture with target modules
  env, vectorstore, web, pipeline, query (+ one-hop deps) loaded.
- Wrote `artifact-inventory.md` — code-verified inventory of every artifact
  the DB layer must re-home, measured against the OA dev environment:
  9 groups (source corpus, build artifacts, web SQLite state, corrections ×2
  + annotations, golden eval, reports, SIRA cell datasets, labels/promotion,
  git-owned config) classified into 5 DB-fit buckets.
- Key findings recorded there: the requirement row is the shared unit across
  parse trees / SIRA corpus / traces / overlay; `out/parse/` is serve-time
  data (ships in every label); sira-query loads cells read-once-into-memory,
  so the DB serves snapshots, not BM25 queries; scale is small (serve-set
  ~40 MB dev) — the driver is operational fit, not volume.

### In progress
- Nothing in code — strand is at the analysis stage.

### Next
- Settle the 5 open questions in artifact-inventory.md, starting with:
  does the corporate serve-set keep the embeddings/Chroma layer, or ship
  SIRA-lane-only? Then: BM25 rebuild-at-load vs per-(cell,label) index blob
  (needs one timing measurement on a production-size cell); one DB vs two
  schemas; build side files-then-publish vs rows-direct; corrections kept
  outside label-scoped immutability.
- Then draft the schema + MODULE.md deltas (doc-first) for the storage seam.

### Flags
- Target corporate DB technology unknown — constrains blob handling and
  the promote/flip mechanism; find out what the corporate network offers
  before schema drafting.

## 2026-10-09 — Storage + converter designs drafted, reviewed, approved, committed

### Done this session
- Architect's scope ruling captured: corp-prod serves the Ask surface only
  (Q&A, feedback, roster, retrieved chunks); Eval Studio, parse review,
  corrections, and the ingestion pipeline stay in dev/test; vector store
  dropped; knowledge graph deferred; SQLite-family DBs preferred.
- Wrote `storage-design.md` (v0.1 → v0.4 through review): two deployment
  profiles behind a `CorpusStore` seam; one write-once SQLite cell DB per
  (MNO, release) version; registry DB as the only mutable corpus state;
  engine-agnostic BM25 design — one file format, three engines (memory /
  postings / fts5), phase-2 eval adjudicates; four gated migration phases
  (architect's plan); phase-4 publish pipeline; §1a overall diagram +
  query/response path.
- Wrote `converter-design.md` (v0.1 → v0.2): label→cell-DB converter,
  schema v1, parity by construction (index built with bm25x's own calls,
  then dumped), mandatory per-cell self-verify, CLI in `sandbox/` (D-111),
  loader knobs `NORA_SIRA_INDEX` / `NORA_SIRA_ENGINE`.
- Review stress tests answered and folded into the docs: global scale
  (~100 MNOs × 40 releases, 10k reqs/cell → ~4,000 cell files, lazy-open +
  LRU loader); RAM per engine (memory engine disqualified as production,
  ~20–30 GB; DB engines ~2–5 GB); single engine per stack at runtime;
  phase-4 publish emits only the winning engine's tables.
- Architect approved the design; expected production engine: postings
  (fts5 ships only if it beats postings in the phase-2 eval).
- Committed (fb87cf6) and pushed; all redaction gates clean.

### In progress
- Nothing in code — strand moves from analysis to build next.

### Next
- Phase 1: implement `sandbox/cell_db_convert` against the current label
  (CNV- error codes, NFR-9 artifact triple, --verify self-check).
- Phase 2: sira-query loader branch (cell-db mode + engine knob), second
  stack via existing A/B compose mechanics, golden + team evaluation.
- MODULE.md deltas for the storage seam when code lands (web, sira-query).

### Flags
- §7 overlay-semantics change (corrections ride the next publish in corp;
  Apply machinery inert during the trial) — approved in review 2026-10-09,
  but promote to a logged decision at land time.
- Corp DB technology / network constraints still unknown — blocks only
  phase-4 items (§8: transfer mechanism, metrics/config DBs, NORA_SURFACE).

## 2026-10-11 — Phases 1–2 built and field-validated; bm25x adjudicated as the engine

### Done this session
- Phase 1 implemented: `sandbox/cell_db_convert.py` converts a promoted
  label's SIRA cells into per-cell SQLite DBs. Schema pivoted to
  blob-primary at implementation — bm25x is a Rust multi-gram hashed
  engine whose ranking a textbook terms/postings dump cannot reproduce;
  the ENRICHED serialized index ships as zlib blobs (~500:1 — the hashed
  n-gram side serializes ~134 MB of mostly zeros regardless of corpus
  size). Per-cell self-verify (in-memory vs DB-loaded rankings must be
  identical), CNV-001..009 codes, content-free report.
- `disable_auto_save()` guards added after every BM25.load (service,
  debug tool, converter): only the constructor form auto-saves today
  (verified empirically; enrich accumulates tf with no dedup), so the
  guards are belt-and-suspenders, pinned by tests.
- Field finding → schema fix: real cells carry duplicate doc/section
  `_id` rows (~80/cell on one corpus); UNIQUE dropped on corpus.req_id —
  the index blobs score every row positionally, so the row set must stay
  byte- and position-faithful; id lookup documented last-wins, mirroring
  the serving loader.
- Phase-1 field validation: 9/9 cells across both stack configurations
  converted + self-verified; 9/9 source fingerprints byte-equal to the
  live stacks' own healthz; deployed index confirmed unigram (max_n=1),
  promoting FTS5 to a real candidate; cell DBs ~10–34 MB.
- Phase 2 implemented: `NORA_SIRA_INDEX=cell-db` loader +
  `NORA_SIRA_ENGINE=bm25x|fts5` (new sandbox/sira_query/cell_db_store.py;
  the designed rebuild-from-rows `memory` engine removed — build params
  neither introspectable nor recorded; the blob engine IS the byte-exact
  parity control). Cell-db cells immutable at serve time (reload 409,
  label variants fall back); healthz identity from DB meta; env knobs
  documented. 89 tests green including the untouched flat-path suite.
- Phase-2 field validation: trial stack healthz gate passed (fingerprints
  + prompt sources equal to its flat counterpart); IDENTITY GATE GREEN —
  0/40 cross-stack result-set diffs (expansion OFF on both stacks for the
  gate: the expansion LLM is nondeterministic at this endpoint, so
  identity is only testable over the deterministic path; expansion-path
  parity carried by converter self-verify + loader tests).
- Engine A/B on a frozen 39-sample denominator (set digest 29088f2a4167,
  shared with the historical sweep): no arm distinguishable — Stage-1
  gaps under campaign floors, Stage-2 inside the judge's own spread
  (first Stage-2 noise floor recorded: mean ±0.5, median ±1.7).
  ADJUDICATED per design rule: production engine = bm25x (byte-exact
  parity); fts5 on record as validated alternate, not shipped.
- Golden data management ruling from field findings: campaign snapshots
  are frozen read-only copies with recorded set digests, outside any
  container mount; the live golden dir stays the web app's working dir.
  Lesson named: a "snapshot" that is a writer's mount point is not a
  snapshot.
- Eval stamp gap fixed (field-found): engine arms shared a knob digest —
  index_mode + engine now ride the healthz retrieval_knobs sub-dict (the
  stamp's service-owns-knobs contract), regression test added.
- docker: serve containers gained SERVE_UID/SERVE_GID (default 0:0 —
  unchanged behavior) so bind-mount writes stop landing root-owned once
  enabled; one-time chown migration documented in env.example.
- Security: corp GitHub alert adjudicated false positive (env-var
  interpolation at service.py Bearer header); whole-history scan of all
  993 commits clean; key-shaped-literal patterns added to the standing
  commit gate.
- Docs synced through the builds: storage-design v0.8, converter-design
  v0.4 (as-built §9 findings + field validation numbers).

### In progress
- Trial stack running (cell-db/bm25x, normal config) — awaiting the
  team-usage round, the last phase-2 criterion.

### Next
- Team-usage round on the trial stack (architect's timing), then declare
  phase 2 closed.
- Ops rollout: SERVE_UID/SERVE_GID on the host stacks (+ one-time chown,
  restart — picks up the stamp fix in the same rebake); dispose of the
  stale 20260819 snapshot dir in the same round.
- Phase 3: `--index=cell-db` ingestion flag with dual-emit, legacy
  default.
- Amend D-DRAFT-2 (engine set is now bm25x|fts5; memory removed) and
  capture this session's new decisions.

### Flags
- Upstream pipeline question (not a converter issue): duplicate
  doc/section rows inflate those rows' BM25 statistics in every stack
  today — the adapter may be emitting section rows once per occurrence;
  investigate in its own session.
- The judge temperature/nondeterminism remains an endpoint property;
  Stage-2 comparisons need the recorded noise floor until a deterministic
  judge endpoint exists.

## 2026-10-16 — Phase 3 shipped and field-debuted; ingestion hardening; enrichment A/B closed

### Done this session
- Phase 3 implemented: `sira_lane --index=cell-db` dual-emits per-cell DBs
  into `<db_root>/cell-dbs/` after the lane + verify complete (converter
  reused in-process, lane run-name pinned, self-verify mandatory); flat
  stays the default. `cycle.sh` forwards `--index` (CYC-E065); per-lane
  build roots became overridable (defaults unchanged). Field debut: a new
  release ingested end-to-end produced its cell DB, self-verified.
- `ingest.sh` gained `-t` (chain taxonomy into the same container/log)
  after a field correction: taxonomy sits outside the ingestion lane, and
  the first launch attempt wrongly assumed a lane flag covered it.
- Taxonomy stage hardened through a field-found defect chain on that
  ingestion: (a) launch visibility — TAX-I001 "N cached / M to extract"
  line plus TAX-W005 (all units stale) and TAX-W006 (prompt-fingerprint
  mismatch discards the ledger — loudly, no longer silently); (b) ledger
  truncation bug fixed — state only saved on extraction events, so a
  zero-extraction retry truncated 267→178 entries; final save now
  unconditional, regression test proven red-without/green-with; (c) the
  whole-stage fingerprint short-circuit had stamped the truncated ledger
  complete — repaired by clearing the stamp (backed up) and re-running;
  the fix restores stamped⇒complete. Root cause of the full re-extract
  itself: an August parser update re-wrote unit bytes but taxonomy never
  re-ran — the re-extract was overdue repair, not waste. Lesson named:
  output-changing parser updates schedule a taxonomy re-extract.
- Enrichment traceability hardened (also field-driven): run-scoped batch
  ids (`<timestamp-rand>.b#####`), `req_ids` on every batch trace row,
  `resp_chars` measured on responses (an earlier response-cap inference
  from config estimates was withdrawn — now it's a recorded field);
  `--req-ids FILE` diagnostic selector (fail-loud). verify-run verdict
  fixed: parse-error batch events WARN only when requests remain
  unresolved — a parse error whose requests all resolved on retry is a
  transient, noted not warned (proven by coverage arithmetic on the new
  cell: all 4,217 docs accounted for).
- Enrichment batched-vs-single A/B designed, run, and closed: at temp 0.4
  exact phrase overlap is ~0 for ALL pairs including same-mode reruns, so
  the standard is word-level Jaccard against the same-mode rerun noise
  floor. Batched sat at the floor (median 0.38 vs 0.38); single-request
  produced ~2× phrases/req (8.6 vs 4.4), exceeding the prompt's 10-cap
  (instruction-only, nothing enforces it), at ~9× the LLM cost, with the
  least shared vocabulary of any arm. Verdict: batched stays production.
  Tooling shipped: `sira_enrich_inspect --compare` (kept counts,
  phrases/req, exact + word-level Jaccard) and a no-best-update patch
  (`NORA_SIRA_SKIP_UPDATE_BEST=1`) after field review caught that
  `update_best` repoints best links on a 0.0 tie AND deletes the old
  targets — experiment runs must never touch best links.
- Finding on the cell dataset's built-in eval: it scores 0.0 everywhere
  on our data (inert) — best-link blessing is OURS via the golden eval,
  never the dataset's own update mechanism.
- Run-selection precedence documented: per-stage pinned run name
  (exact-or-nothing) > use-latest > best-links/canonical; stacks that set
  both pins and use-latest are running on pins — latest is dead config
  there. Pin-miss degrades silently; migration to per-cell best-link
  pinning queued for the maintenance window.
- Ops/security: `serve-up.sh` launcher exports the launching user's
  user/group ids to the serve containers (root default unchanged); one defunct
  compose stack discovered (lost its port race months ago, no network,
  zero exposure) — teardown queued for the maintenance window; one
  credential-scanner false positive resolved by rewording two comments.
- Docs synced: storage-design v0.9 (§5 phase-3 as-built; §7
  backward-compat invariant — all changes land behind flags with legacy
  defaults; §8 taxonomy/parse artifact disposition row: cell DBs stay a
  SIRA-lane product, parse/taxonomy re-homing deferred to phase 4).

### In progress
- Trial stack still serving cell-db/bm25x; the new release's cell is
  built + verified but not yet serving (restart pending).

### Next
- Serving restart to pick up the new cell; team-usage round (last
  phase-2 criterion), then declare phase 2 closed.
- Compare-tool field validation: reproduce the A/B comparison table on
  the work host after the next merge.
- Maintenance window (one batch): per-cell best-link pinning + golden-
  eval blessing (includes repairing dangling best links), SERVE_UID
  rollout to the flat stacks + one-time chown, defunct stack teardown,
  experiment-dir deletion, stale snapshot disposition.
- Phase 4 remains parked on corp network facts.

### Flags
- Dangling best links exist on one corpus (cause identified:
  wipe-stale-index + full-resume combination) — repair deliberately held
  for the blessing step, not patched ad hoc.
- The enrichment phrase cap is prompt-instruction only; nothing enforces
  it mechanically. Surfaced by the A/B; left as-is while batched (which
  respects it in practice) is production.
- Optional parked study: single-request-style richer enrichment vs
  batched, judged by golden eval rather than overlap metrics.

## 2026-10-18 — serve-path hardening after host-user serving

### Done this session
- Host: all three stacks serve as the host user (SERVE_UID/SERVE_GID);
  no root-owned files in any mount before or after.
- Built, held for commit: the team gate runs only `section=merged` for
  gated members and refuses `synthesize-group`; no HuggingFace default
  embedder or reranker (web route, env config, VectorStoreConfig,
  make_embedder, golden/query/eval CLIs); compose sets HOME and
  HF_HUB_OFFLINE on the serve services; TEI base-URL docs; promote.sh
  copies the NORA vectorstore.
- Decided: no refusal fallback on web (the user changes the model by
  hand).

### In progress
- The held batch awaits commit; host steps follow it (rebuild, gate
  and no-index checks, re-copy existing labels' vectorstores, drop the
  web fallback env lines).

### Next
- Decide whether to empty config/llm.json's stale embedding entry.
- Check whether SIRA cell files change on load (same fix as chroma if so).

## 2026-10-18 — serve-path batch shipped and verified; embedding defaults gone from both llm.json files

### Done this session
- Shipped the held batch (`9589a14`) and verified it on all three stacks:
  the gate refuses every section but `merged` for gated requests; a NORA
  lane with no index fails at once with "No NORA index"; compose alone
  supplies HOME and HF_HUB_OFFLINE; every web and pipeline env file names
  the embedding provider; stack-A's two labels hold their own vectorstore
  copy (1 link per file).
- SIRA cell files do not change on load, so promote keeps hardlinking them.
- `0a3115e`: config/llm.json no longer sets an embedding provider or model.
  The deployed web roster (the internal-only llm.json the web image reads)
  had the same stale fields; they are emptied there too. Every container
  now resolves the embedder from its env file.
- Found: on the roster path the refusal reroute is switched by
  NORA_LLM_REFUSAL_MARKERS alone; removing NORA_LLM_FALLBACK_* does not
  turn it off. Web env template says so (`0a3115e`); the markers were
  removed from the stacks that still set them.
- `29364d6`: docker README says NORA_LLM_CONFIG is a web-only setting and
  the pipeline image reads config/llm.json.
- After the same-day drift checks (`0b030b5`, `2f166d3`), the stacks were
  rebuilt at that code: the pipeline context resolves the embedder from
  env on both pipeline env files; all bound profiles set
  toc_page_threshold, so the default change reaches none of them.
- Stack-A's sira-query was two months behind; rebuilt to current code.
  Health-check keys gained the flat engine/index-mode fields; no retrieval
  value changed. Stack-A's sira-query stays without a refusal fallback
  (decided).

### In progress
- None.

### Next
- Sync messages name every image to rebuild, including sira-query, or
  say it is left alone.

### Flags
- 3 sira verify-run tests fail on a clean main (pre-existing, unrelated).

## 2026-10-21 — web refusal fallback restored; all three images rebuilt

### Done this session
- Reversed the 2026-10-18 "no refusal fallback on web" decision
  (D-DRAFT-19): the web refusal fallback is active again on all three
  stacks, Ask page only. NORA_LLM_REFUSAL_MARKERS is set in each web env
  file (stack-A for the first time); the roster's fallback_provider
  supplies the endpoint. The curation chat (no-roster path) stays without
  a fallback: no NORA_LLM_FALLBACK_* lines. Each stack logs "Refusal
  fallback active".
- All three images rebuilt on all three stacks at code that includes the
  decision-model merge (knob unset; sira-query health check gains
  query_analyzer = rules, no other value changed). sira-query is now named
  explicitly in every sync request.
- Host-user serving ticked done in the channel-side checklist.

### In progress
- None.

### Next
- Team-usage round on the trial stack (closes phase 2), then the
  maintenance window.
- Upstream parse item: join PDF tables split at a page break.

### Flags
- 3 sira verify-run tests fail on a clean main (pre-existing, unrelated).

## 2026-10-21 — phase 2 closed; phase 4 opened

### Done this session
- Team-usage round completed clean: phase 2 closed (identity gate,
  recall and judge checks, bm25x engine, team round all met).
- Open points resolved: the team-mode gate replaces `NORA_SURFACE`
  (D-DRAFT-20); corp corrections ride the next publish (D-DRAFT-21).
- Phase 4 scoped: 4a cell DBs become the default and official locally,
  flat stacks retire; 4b publish pipeline built and tested against a
  stand-in corp volume; 4c corp deployment waits on open points 2 and 3.

### Next
- Phase-4 plan (4a + 4b), then implementation.

## 2026-10-21 — store split designed; phase-4 plan rewritten

### Done this session
- Design `store-split-design.md`: four stores (Cells, Trees, History,
  Overlay) behind interfaces with local and HTTP implementations; index
  composed at load from vanilla + SIRA + Overlay with a disposable cache;
  shared `req_fingerprint`; SQLite for all four (D-DRAFT-22..26).
- Overlay becomes a live store: D-DRAFT-21 withdrawn.
- `phase-4-plan.md` v1: 4a store split (S1–S6), 4b publish (B1–B8).

### Next
- Review the design; then S1–S3 and the trial checkpoint.

## 2026-10-22 — S1–S3 checkpoint passed on trial; trial serves store-split

### Done this session
- S1–S3 and the deep rename on branch `store-split`: shared
  `req_fingerprint`; cell DB schema 2 (vanilla index + raw SIRA phrases +
  fingerprints, overlay composed at load); `core/src/stores` Overlay store
  (JSON default, SQLite opt-in); images `nora-store` / `nora-batch`,
  package `nora_store`, route `/query`, `NORA_STORE_*` service variables.
- Trial checkpoint passed and trial stays migrated: identity gate 25/25
  probes on all 5 cells; data fingerprints 5/5 and enriched docs equal;
  0 of 21 retrieval knobs changed; health ok with both prompts; gated Ask
  200; review Apply on cell-db works (was 409 on schema 1).
- Golden Stage-1 within noise, not identical: two runs with identical
  stamps differ on 36 of 39 samples (LLM query expansion varies); recall
  0.683 before, 0.660 / 0.678 after.
- Fixed along the way: package-move path bugs (clone root, git root);
  dangling `best.jsonl` refused (CNV-011); missing configured prompts fail
  health and the image build; prompt presence keys golden runs; an empty
  store-URL value counts as unset.

### Next
- S4 Trees store, S5 History, S6 store HTTP routes and clients.
- Merge `store-split` to main, then migrate stack-A and stack-B.
- `nora-store-test` build target with pytest so the bm25x suites run.

### Flags
- The converter needs bm25x, which `nora-batch` lacks; it runs in the
  `nora-store` image for now (argues for merging the two batch images).
- Store default prompts are `_v01` while the batch lanes use `_v02`; moving
  the store default changes Stage-1 behaviour and needs a new baseline.
