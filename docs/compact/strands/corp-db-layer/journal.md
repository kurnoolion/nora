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

## 2026-10-22 — document and query prompts were on different versions

### Flags
- Documents were enriched with the `_v02` doc prompt while every serving
  stack expanded queries with the `_v01` query prompt (the store's default
  path; the `_v02` files come from an internal install step). Only the
  query-expansion prompt affects retrieval today: LLM rerank is off.
- The prompt-scheme tag (`scheme-v1` / `scheme-v2`) is set at promote time
  and says nothing about the serving prompts.

### Done
- Serving moves to the `_v02` query and rerank prompts by deployment
  configuration (`NORA_QUERY_PROMPT` / `NORA_RERANK_PROMPT`), so both sides
  use one version.
- `query_prompt_sha` / `rerank_prompt_sha` in the store's retrieval knobs:
  a prompt-version change now breaks golden-run comparability.

### Next
- A new Stage-1 baseline per stack after the switch (repeat runs: query
  expansion is run-to-run noisy).

## 2026-10-23 — 4a checkpoint settled deterministically; 4b designed

### Done this session
- With query expansion off, the 4a store and the pre-4a store retrieve
  identically on the frozen golden set: 0 of 39 samples differ, recall /
  r@5 / r@10 equal to four decimals; stamps differ only in code version.
  The earlier Stage-1 spread was expansion noise. The expansion-off
  Stage-1 becomes the publish gate's deterministic component.
- Trial serves phase 4a from a host-only merge of main (`7505ebb`+) with
  the company main; nora-web is a pure client (all three stores `http`);
  Trees 468 docs / 50293 nodes, History built for 3 MNOs.
- Field fixes on main: converter stamps `fingerprint_version` and warns
  when the adapter sidecar is absent; History change rows carry `req_id`
  and `/req/last-changed` takes `key=`; conditional Trees/History mounts
  in compose; `nora-store-test` build target.
- Phase 4b publish design agreed (`publish-design.md`, D-DRAFT-30).

- Stack-B migrated to 4a (flat → cell-db/bm25x) and is the Overlay
  authority; expansion-off Stage-1 before/after: 0 of 39 differ. The
  company main carries the install-both prompt commit and is synced with
  origin; a pinned old-tooling worktree serves stack-A until it moves.
  `nora-store-test`: 189 passed, first real run of the bm25x suites.

### In progress
- Stack-A migration to 4a; its store reads the Overlay from stack-B via
  the host gateway.

### Next
- 4b implementation B1–B8 after the A/B round.

### Flags
- Labels adapted before the fingerprint sidecar carry no `v2:`
  per-requirement fingerprints until their next SIRA build; review edits
  there stay Jaccard-guarded.

## 2026-10-23 — 4b built and proven; every stack serves the store design

### Done this session
- Phase 4b implemented and tested: `registry.db` (store versions,
  release sets, gate runs, active, flips), the publish CLI (publish /
  gate / flip / rollback / gc / status, pluggable transfer),
  registry-mode serving in nora-store (`NORA_STORE_REGISTRY`,
  `NORA_STORE_RELEASE_SET`, `POST /registry/reload`, healthz
  `registry_*`), registry fallback in the Trees/History factories and
  the standalone host; compose `SERVE_REGISTRY_ROOT`. 2873 tests pass.
- End-to-end on trial: 13 units published into a stand-in serving root,
  gate and flipped store both 0 of 39 differ against the 4a run,
  rollback drill (2 sets, 3 flips), GC dry run clean; the label
  directory never written.
- Field fixes from the rounds: publish warns on Trees units without a
  Cells unit; a store started with no active set loads its prompts and
  the first reload repairs an older image; an unopened store reports no
  change on reload; the batch image bakes `NORA_CODE_VERSION`.
- All three stacks moved onto their own registries with retrieval
  provably unchanged (0 of 39 differ, 0 knob values changed on each);
  the Overlay authority and trial moved onto the SQLite Overlay with
  digests equal and edits from every web landing in `overlay.db`. The
  strand's goal — every nora stack serving the new store design live —
  is met.
- Docs: `publish-design.md` v1.1 (§9 refinements), docker README with
  the registry as the serve path and the Overlay import runbook, env
  examples, stores MODULE.md.

### In progress
- Host retirement of the flat path: stale label pointers out of the
  wiring envs (one recreate per stack), then after a soak the env
  backups, the 4a image sets and the label directories; flat cell
  inputs stay until the next build cycle.

### Next
- Land the strand. Corp transfer (remote `Transfer`, first corp publish,
  backend choice) is a later strand by the architect's call.
- Rebuild the two nora-web images that predate 4b at the next
  convenient build (no behaviour change; keeps images on one commit).

### Flags
- Labels adapted before the fingerprint sidecar still carry no `v2:`
  per-requirement fingerprints; review edits there stay Jaccard-guarded.
- Query expansion is non-reproducible run to run (golden-eval strand);
  the expansion-off Stage-1 is the only deterministic gate.

## 2026-10-25 — Architecture document; docs brought to the as-built state

### Done this session
- `/drift-check dev-module stores`: 2 drifts, both Public surface under-
  declared after 4b (Registry `exists`/`root`/`next_version`, `flip`
  return; publish CLI `--note`, rollback `--store-url`); MODULE.md
  updated to the code.
- nora-store: healthz `sira_prompt_scheme` now reads the served release
  set's `prompt_scheme` after the env override (publish --prompt-scheme
  reaches the eval stamp); the two comments calling nora-web the
  Overlay's sole writer rewritten to the authority model; test added.
- `docs/architecture.md`: the software and data architecture in five
  parts — planes and images, per-process interfaces, identity and every
  store schema, six end-to-end flows (ingest, publish, serving per query
  pattern, review, golden eval, two stacks), 22 enforced invariants,
  known gaps, glossary. Supersedes this strand's storage, store-split,
  store-schema, publish and converter notes as the reference.
- Docs moved to the registry path and the Overlay store: ingestion guide
  (6p publish, 6g gate, 6f flip), provisioning checklist (serving roots,
  B2 per-stack steps), docker README (registry leads Phase 6; flat path
  as dev/test; --store-url by service name), review and golden guides,
  sandbox README and SETUP, README step 8, structure-conventions (serving
  store layout; stage dir names), web and env MODULE.md. System Design
  Document updated to 1.2 draft outside the repo (figures still 1.1).

### In progress
- Host retirement of the flat path after the soak (unchanged).

### Next
- Land the strand; then `requirements.md` with the canonical IDs
  (FR-29, FR-36, FR-40, FR-43 rewording; new FRs for the Trees and
  History stores, the single-writer Overlay, write-once store files).
- Redraw the five System Design Document figures that still show the
  snapshot model; TDD appendix rows; PROJECT.md contributor row.

### Flags
- Build is not incremental across cycles: a new build dir re-parses and
  re-enriches every cell; only publish reuses by fingerprint. The
  ingestion guide's "only that cell" claim holds within one build.
- `standards` runs unless `--skip-standards`; not off by configuration.
- FR-40 names select-synth as the serving path; the code default with
  `NORA_SIRA_SYNTH_MODE` unset is rerank-pin (needs the vector store).
- History has no consumer in nora-web; release-diff questions are
  answered by retrieval over two cells (release-history strand).
- Golden: no target values in code; `run_ref` is free text; Stage-1
  expansion non-reproducible (carried from before).
- `nora_store` has no MODULE.md.
- `README.md` and `sandbox/SETUP.md` carry MNO codes and release labels
  on lines predating this strand.
