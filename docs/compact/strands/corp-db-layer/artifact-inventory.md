# Artifact inventory — what the DB layer must re-home

Strand: `corp-db-layer` · Analysis date: 2026-10-08 · Status: draft for review

**Method.** Enumerated from code, not from memory: `env.config.ENV_DIR_DIRS` +
`pipeline.stage_output` (build side), `web/config.py` + compose mounts
(serve side), `promote.sh` / `serve-push.sh` / `serve-flip.sh` (labels),
`sandbox/sira_query/service.py` (SIRA cell datasets). Sizes measured on the
OA dev environment (single MNO, one release) — order-of-magnitude only; the
production corpus is larger but the same shape.

**Scope.** Everything that today assumes a local filesystem or an env-dir
path convention (D-022). Committed configuration (profiles, mappings,
prompts templates, roster, `config/*.json`) stays in git and is out of
scope — it reaches runtime through images, not through the data layer.

---

## A. Source corpus — `<env_dir>/input/` or `REQUIREMENTS_DIR` mount

| Artifact | Producer | Consumers | Format | Lifecycle | Size (dev) |
|---|---|---|---|---|---|
| `input/<MNO>/<release>/*.pdf\|docx\|xlsx` | humans (release drop) | extract stage only | source binaries | append-only per quarter; read-only mount in containers | 20 MB |

- Path convention is load-bearing: `infer_metadata_from_path` derives
  `(mno, release)` from the directory pair and fail-louds on a non-MMMYYYY
  release dir (`EXT-E004`). Any DB-era intake keeps this metadata explicit.
- **DB-fit: object store (or stay a file share).** Only the extract stage
  reads it, once per cycle. A corporate file share or blob bucket with the
  same two-level key is enough; nothing queries into the binaries.

## B. Build artifacts — `<env_dir>/out/<stage>/`

Per-cell stages write under `out/<stage>/<mno>/<release>/`; global stages
write flat (`pipeline.stage_output`, exhaustive partition). Everything here
is **rebuildable from A + committed config**, which is the single most
important fact for the migration: the build side can keep writing files on
the build machine, and only the *serve-set* needs the DB.

| Stage dir | Files | Producer → consumers | Mutability | Size (dev) |
|---|---|---|---|---|
| `out/extract/` | `<doc>_ir.json` per document + `images/` | extract → profile, parse | immutable per run; skip-if-newer reuse | 5.3 MB |
| `out/profile/` | `profile.json` per cell (substituted) | profile → parse | re-materialized each run | 8 KB |
| `out/parse/` | `<doc>_tree.json` per document | parse → resolve, taxonomy, graph, vectorstore, SIRA adapter, **web at serve time** | immutable per run; `profile_fingerprint`-gated reuse | 1.7 MB |
| `out/resolve/` | `<plan>_xrefs.json` manifests | resolve (OFF) → graph | stage disabled in current builds | 208 KB |
| `out/taxonomy/` | `<plan>_features.json`, `taxonomy.json`, `extraction_state.json`, `.corpus_fingerprint` | taxonomy → SIRA enrichment prompts, graph | cache keyed by corpus fingerprint; `extraction_state.json` is the per-unit resume ledger (mutable) | 44 KB |
| `out/standards/` | `TS_<spec>/Rel-<N>/` spec docs + extracted sections | standards (OFF) → graph | download cache + derived JSON | 76 MB |
| `out/graph/` | `knowledge_graph.json` | graph (OFF) → query | rebuilt per run | 3.2 MB |
| `out/vectorstore/` | per-cell ChromaDB (`chroma.sqlite3` + segment dirs), `config.json`, `build_stats.json` | vectorstore → NORA retrieval lane, Test-page inventory, golden CLI | rebuilt per run | 35 MB |
| `out/eval/` | eval-stage outputs | eval stage | per run | 24 KB |

Notes that change the design:

1. **`out/parse/` is a serve-time dependency, not just a build intermediate.**
   `web/req_tree.py` (`find_req`, pickers, req browser) reads the parse trees
   directly; `promote.sh` ships `out/{vectorstore,graph,taxonomy,parse}` in
   every label for exactly this reason. In a DB world the parse layer becomes
   first-class serving data: a `requirements` table (one row per Requirement,
   keyed `(label, mno, release, doc_id, req_id)` with section/parent/plan
   metadata and body text) replaces every tree-file scan. `find_req` is
   today a corpus-wide linear scan over JSON files — the DB turns it into an
   indexed lookup, which is a straight win.
2. **The requirement row is already the natural unit everywhere.** Parse
   trees are a flat `requirements` list; SIRA's corpus is one JSON line per
   requirement; enrichment traces are per requirement; overlay records are
   per `(mno, req_id)`. One canonical requirements table can back all of
   them, with per-consumer projections.
3. **The three OFF stages (resolve, standards, graph)** write file artifacts
   today. The DB schema should *reserve* their shapes (cross-ref edges,
   standards sections, graph edges are all row-shaped) but not block on them.
4. **ChromaDB is the one artifact that is already a database** (SQLite +
   binary segments) but an *embedded* one. Its only consumers are the
   NORA-native retrieval lane (no longer on the Ask page) and the corpus
   inventory counts. Scope question for the design: does the corporate
   serve-set need embeddings at all, or do we ship the SIRA lane only and
   drop 35 MB+ of per-cell Chroma from the serve path? (The Ask page's
   single query path needs: SIRA cells + requirements table + roster.)

## C. Runtime web state — SQLite, per stack and pooled

| Store | Default path (bare) / container path | Writer → readers | Shape |
|---|---|---|---|
| Jobs | `state/nora.db` / `/data/web-state/nora_jobs.db` | web (aiosqlite, WAL) | 2 tables (jobs, logs) + indexes |
| Metrics | `state/nora_metrics.db` / `/data/web-state/nora_metrics.db` | web middleware + sampler, fire-and-forget | 1 table, 5 categories, time-indexed |
| Config | `/data/web-state/nora_config.db` | web `/config` page (sync sqlite3 + lock) | key-value `(module, key) → JSON` |
| Feedback | `state/nora_test_feedback.db` / `/data/feedback/nora_feedback.db` (**pooled across stacks**, D-120) | web | Q&A rows + per-lane feedback columns |

- **DB-fit: near-mechanical port.** These are already relational; the
  schemas are small and owned by one writer each. Moving to a corporate
  RDBMS mostly means swapping aiosqlite for an async driver and keeping two
  properties: metric writes must stay fire-and-forget (never block a
  request), and jobs/metrics must stay separable for retention (today:
  separate files; tomorrow: separate tables with independent retention).
- The per-stack vs pooled split must survive: jobs/metrics/config are
  per-stack identity; feedback (and golden) are deliberately shared. In one
  corporate DB that's a `stack_id` column vs its absence.

## D. Corrections — two distinct systems, same persistence principle

| System | Path | Writer → readers | Semantics |
|---|---|---|---|
| Pipeline corrections | `<env_dir>/corrections/{profile,taxonomy}.json` | human (web editor) → pipeline stages | full-copy override; stage prefers correction over its own output on every rerun (D-011) |
| Enrichment overlay | `<corrections_root>/sira-enrich/<MNO>.json` + `accepted-labels.json` | web `EnrichOverlayStore` (sole writer, flock + atomic rename) → sira-query (ro, folds at cell load) | per-(word, direction) records; labels-are-branches; merge log; view digest formula byte-locked across both images |
| Bootstrap annotations | `<env_dir>/annotations/<plan>_annotations.json` | human (Bootstrap tab) → parser pre-pass | per-doc annotation records, atomic write |

- **These are the most DB-shaped files in the system** — record-oriented,
  concurrent-edit-prone, with hand-rolled locking that a DB gives for free.
  Overlay records become rows keyed `(mno, req_id, word, direction, label)`;
  the merge log a `labels` table; "pending" becomes a digest over a view
  query (the formula-parity invariant with sira-query must move with it —
  both sides must compute the digest over identical row serialization).
- Corrections must stay **durable across labels and builds** (they pool
  across stacks today). They are the one category that is explicitly NOT
  versioned by label — a correction made once applies to every later run.

## E. Eval — golden set and runs

| Artifact | Path | Writer → readers | Shape |
|---|---|---|---|
| Golden samples | `<env>/eval/golden/samples/gs-NNNN.json` (pooled mount) | web Eval Studio via `eval/golden.py` only | one JSON doc per sample; status workflow draft → stage1-ready → golden-ready |
| Golden runs | `<env>/eval/golden/runs/<run_id>/report.json` | golden CLI/runner | immutable run reports + StackStamp |
| Legacy eval workbooks | `<env_dir>/eval/*.xlsx` | experts | read-through loader |

- Samples are per-file JSON *specifically to avoid merge conflicts between
  concurrent experts* — a DB table gives that natively (row-level
  concurrency), so this motivation transfers cleanly. The single-owner rule
  (web writes only through `golden.py`'s schema) should become "web writes
  only through the eval schema module", preserving one validation path.
- Runs are append-only and comparability-keyed (`stage1_key`/`stage2_key`
  from the StackStamp); as rows they become queryable across releases —
  the accuracy-over-time tracking §4.7 promises gets easier, not harder.
- Proprietary content (queries, req_ids, golden text) — same NFR-8 posture
  in the DB: no content in logs or error messages; access via the gated app.

## F. Reports and logs — stay out of the DB

`<env_dir>/reports/` (lane logs, `parse_log/*.json`, tax_debug captures,
CYC phase blocks), 2 MB on dev. Diagnostic, per-build, redaction-sensitive
(verbose logs may carry content and are explicitly disk-not-chat). These
remain build-machine files or go to the corporate log system; nothing reads
them at serve time. The parse transparency log is the only one with a UI
(Parse Review page) — it rides the build, not the label.

## G. SIRA cell datasets — `<db_root>/<mno>__<MMMYYYY>/` (the serve-critical blob)

Per cell (from `service.py` `_load_one_cell`):

| File | Content | Consumer behavior |
|---|---|---|
| `raw/corpus.jsonl` | one line per requirement: `_id`, `title`, `text` (enrichment-bearing) | loaded fully into memory at cell load |
| `index/best/` | bm25x binary index over the corpus | `BM25.load()` at cell load; vanilla index, enrichment applied on top |
| `runs/doc-enrich/<run>/enrichments.kept.jsonl` (+ run metadata, per-req trace rows) | kept enrichment phrases per requirement + resume ledger | phrases folded into the loaded index; trace is the requirement-level resume unit |
| `enrichments/doc/best.jsonl` | promoted-best fallback phrases | fallback when no pinned run |
| adapter inputs (`queries.jsonl`, `qrels/test.tsv`, `metadata.json`) | eval fixtures per cell | batch/eval only |

- **Load pattern is "read everything once, serve from memory".** The service
  never random-reads these files after startup; reload is an explicit
  `POST /cells/<cell>/reload`. So the DB does not need to serve BM25
  lookups — it needs to serve a *consistent snapshot per (cell, label)* that
  the service loads once. Two viable shapes, to be decided in design:
  (a) rows (requirements + phrases) in the DB and the service builds the
  BM25 index in memory at load — one source of truth, pay index-build time
  per load; (b) rows + a prebuilt index blob per (cell, label) — faster
  load, blob must be byte-stable with the rows. The nora lane already does
  (a) (`BM25Index.from_store`), which is evidence (a) is workable.
- The healthz `data_fingerprint` (per-cell + aggregate) and serve MANIFEST
  facts must be derivable in the DB world — they feed StackStamp and eval
  comparability. A label row carrying the fingerprint replaces hashing files.

## H. Labels, promotion, cycle state — the semantics to preserve

| Artifact | Today | DB-era equivalent |
|---|---|---|
| `serve/<label>/` hardlink snapshot (`nora/out/{vectorstore,graph,taxonomy,parse}` + `sira/<cells>`) | `promote.sh`; immutable by convention | `label` row + all serving tables keyed by `label_id`; promote = publish rows under a new label_id, then flip the stack's active-label pointer |
| `MANIFEST.json` (builds, git sha, timestamp, prompt scheme; copied into each subdir) | identity for healthz/StackStamp | columns on the `label` row |
| `serve-push.sh` (rsync + checksum, atomic rename) | cross-host transfer; complete-or-absent | disappears if build and serve share the corporate DB; else becomes a bulk-load with the same complete-or-absent guarantee (transaction / staging schema) |
| `serve-flip.sh` | flip stack → label, verify reported identity | `UPDATE active_label` + the same healthz verification |
| `CYCLE.json` + `<builds>/ACTIVE` baton | one cycle in flight, owner, phase log | a `cycles` table enforcing the one-in-flight constraint |

The invariants that must survive verbatim: **labels never change after
creation** (no UPDATE/DELETE on label-scoped rows — enforceable with DB
permissions, which is *stronger* than today's convention); **rollback is
repointing, not restoring**; **promotion is attributable** (who/when/why —
today the promote log, tomorrow columns); **a label arrives complete or not
at all**.

## I. Explicitly out of scope / unchanged

- Committed config: profiles (`customizations/profiles/`), mappings and
  prompts (internal repo, D-062), LLM roster (`customizations/llm/llm.json`,
  baked into image, D-248), `config/{web,llm,retrieval}.json`. Git-owned.
- Browser localStorage (ask history, D-208) — client-side by design.
- `state/cline-mapping.json` — on-prem tooling state, not runtime data.
- Models (`MODELS_DIR`, docling artifacts) — image/host provisioning.

---

## Synthesis — classification for the DB design

| Bucket | Artifacts | Verdict |
|---|---|---|
| 1. Already relational | jobs, metrics, config, feedback (C) | port schemas near-verbatim; keep fire-and-forget writes and per-stack vs pooled split |
| 2. Row-shaped JSON wanting to be tables | parse trees → requirements table (B2); SIRA corpus + enrichment phrases + traces (G); overlay records + labels (D); golden samples + runs (E); taxonomy features (B); resolve manifests / graph edges / standards sections (B, reserved for the OFF stages) | the core new schema; requirement row is the shared unit |
| 3. Binary/index blobs | bm25x index, ChromaDB segments, extracted images, standards doc cache | rebuild-from-rows at load (preferred where measured-fast) or blob store keyed by (cell, label) |
| 4. Build-side ephemera | extract IRs, parse logs, reports, eval-stage outputs | stay on the build machine / object store; never in the serving DB |
| 5. Semantics, not storage | label immutability, promote/flip/rollback, one-cycle baton, corrections-survive-labels, cell isolation, digest parity | the actual design work — schema keys (`label_id`, `(mno, release)` cell, `stack_id`) and permissions |

**Measured scale (dev env, single MNO):** serve-set ≈ 40 MB + SIRA cells;
whole `out/` ≈ 120 MB with the OFF stages' 76 MB standards cache; state
≈ 3 MB. Even at 10× for the production corpus this is a *small* database —
every candidate corporate RDBMS is comfortable, and whole-corpus-in-memory
loading at service start remains viable. The driver for the DB layer is
**operational fit on corporate infra** (no env-dir mounts, managed backup,
cross-host consistency), not data volume.

**Biggest design questions surfaced by the inventory** (for the next
session, before schema drafting):

1. Does the corporate serve-set include the embeddings/Chroma layer at all,
   or is it SIRA-lane-only? (Decides whether bucket 3 has a Chroma problem
   or not.)
2. BM25: rebuild-at-load from rows vs index blob per (cell, label) —
   measure index build time on a production-size cell to decide.
3. One DB or two: serving reads (labels, requirements, cells, overlay) have
   a different availability/permission profile than web runtime state
   (jobs/metrics). Same instance with separate schemas is the likely answer;
   confirm against what the corporate network offers.
4. Where does the build side run in the corporate world — does the pipeline
   write files then bulk-publish (keeps D-022 locally, smallest change), or
   write rows directly (bigger change, kills the file layer)?
5. Corrections durability: pooled-across-stacks and label-independent today;
   the DB must keep them out of the label-scoped immutability rule.
