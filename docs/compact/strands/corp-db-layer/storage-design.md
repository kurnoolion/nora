# Storage design — dev/test and corp-prod deployment

Strand: `corp-db-layer` · Draft v0.5, 2026-10-09 · Status: approved in review
(v0.2: engine-agnostic cell schema, registry tiers + scale analysis,
phased validation plan replacing the big-bang publish sequence.
v0.3, post-review: single engine per stack made explicit; `memory`
disqualified as production engine at global scale; lazy-open/LRU loader
contract; single-engine publish in phase 4; inverted-index glossary
note. v0.4: §1a overall-design diagram + query/response path.
v0.5: backward-compatibility invariant in §7 — all phase-1–3 changes
additive and flag-gated, today's behavior the default.)

Inputs: `artifact-inventory.md` (same strand) + the architect's scope
ruling of 2026-10-09: corp deployment serves the Ask surface only
(Q&A, feedback, roster/model selection, retrieved-chunk display);
Eval Studio + golden runs, parse review, and enrichment corrections stay
in the dev/test environment; the ingestion pipeline stays in dev/test;
deployment pushes updated corpus into corp DBs incrementally; corp data
layer prefers standard embedded DBs — SQLite wherever possible, a
use-case-optimized engine where one earns its place (BM25).

## 1. Shape of the answer

Two deployment profiles, one codebase, one new seam:

- **dev/test (today's Linux PCs)** keeps the entire current world:
  env-dir file layout (D-022), the 9-stage pipeline, SIRA batch lane,
  Eval Studio, parse review, enrichment corrections, promote/serve
  rehearsal. Nothing about the build side changes.
- **corp-prod** runs the two serve containers only (nora-web with the
  Ask surface, sira-query), reading a **SQLite-family data layer**:
  one corpus DB file per (MNO, release) cell version, a small registry
  DB that says which versions serve, and a pooled feedback DB.
- The seam between them is a **storage protocol** in code (same pattern
  as `LLMProvider` / `VectorStoreProvider`): a `CorpusStore` with a
  filesystem implementation (today's behavior, default) and a SQLite
  implementation (corp). Config selects the backend, so dev can run the
  corp backend locally for pre-prod parity testing.

Dropped from the serve-set entirely, per the scope ruling: the Chroma
vector store (no corp footprint at all; the pipeline stage may still run
in dev for experiments), the knowledge graph (schema reserved, revisit
later), the jobs DB (no ingestion in corp), parse review / annotations /
pipeline corrections / overlay / golden authoring (dev-only surfaces).

## 1a. The picture — overall design and the query path

```
 DEV/TEST (build side, unchanged)              CORP-PROD (serve side)
┌─────────────────────────────────┐    ┌──────────────────────────────────────┐
│ env-dir pipeline, SIRA batch,   │    │        user (Ask page)               │
│ Eval Studio, corrections,       │    │          │ question      ▲ answer +  │
│ golden authoring                │    │          ▼               │ chunks    │
│        │ verified build         │    │   ┌─────────────┐        │           │
│        ▼                        │    │   │  nora-web   │────────┘           │
│ ┌─────────────────┐  cell DBs   │    │   │ (Ask only)  │──▶ feedback.db     │
│ │ corpus_publish  │─(new/changed│    │   └──────┬──────┘                    │
│ │ (grown from the │  cells only,├────┼─▶        │ /query (cell, question)   │
│ │  converter)     │  checksum + │    │          ▼                           │
│ └─────────────────┘  rename)    │    │   ┌─────────────┐   ┌─────────────┐  │
└─────────────────────────────────┘    │   │ sira-query  │──▶│ corp LLM    │  │
                                       │   │             │   │ endpoints   │  │
                                       │   └──────┬──────┘   └─────────────┘  │
                                       │          │ CorpusStore (sqlite)      │
                                       │          ▼                           │
                                       │  ┌────────────────────────────────┐  │
                                       │  │ registry.db  (active pointers; │  │
                                       │  │  the only mutable corpus state)│  │
                                       │  │ cell-<mno>-<rel>-vN.db × many  │  │
                                       │  │  (write-once; lazy open + LRU) │  │
                                       │  └────────────────────────────────┘  │
                                       └──────────────────────────────────────┘
```

**Query and response path** (one question, `postings` engine shown;
the engine choice changes only steps 4–5):

1. **nora-web → sira-query.** The Ask page posts the question and the
   selected cell to sira-query's `/query`.
2. **Resolve and open the cell.** The loader asks `registry.db` for the
   cell's active version → filename. If the cell is not already open:
   open the file (`mode=ro, immutable=1`), read `meta` (k1, b, N,
   avgdl, fingerprint, schema check) and `doclen` — milliseconds,
   <1 MB. The cell moves to the front of the LRU; past the open-cell
   budget, the least-recently-queried cell is closed (§2.1).
3. **Question enrichment (LLM).** One LLM call proposes expansion
   phrases for the question, as today.
4. **DF-filter the expansion.** `SELECT df FROM terms WHERE term IN
   (…)`; expansion terms with df above the cell's max-df
   (`max(1, N × ratio)`) are dropped — same rule, same per-cell
   statistics as today.
5. **Score.** `SELECT term, doc, tf FROM postings WHERE term IN (…)`
   for the surviving original + expansion terms; the service
   accumulates the exact bm25x formula from the fetched rows and the
   `meta` params, combining `s_orig + w·s_exp` per
   `search_with_expansion` semantics; top-k wins. (`fts5` engine: two
   MATCH queries combined in the service instead; `memory` engine: no
   DB reads at query time — the in-memory index built at open answers
   directly.)
6. **Materialize.** `SELECT req_id, title, text FROM corpus WHERE doc
   IN (top-k)` — the retrieved chunks.
7. **Rerank + answer (LLM).** Optional rerank call, then the answer
   call over the top chunks, as today.
8. **Respond.** sira-query returns the answer and the retrieved chunks
   to nora-web; the page renders both. Req-ID bubbles resolve through
   the same seam (`CorpusStore.find_req` → one indexed `requirements`
   lookup). User feedback writes to the pooled `feedback.db` — the
   only runtime write in corp; the cell files are never written.

Steps 3, 7, and the response shape are untouched by this design; the
storage layer replaces only where steps 2, 4–6, and the bubble lookup
get their data.

## 2. Corp data layer

### 2.1 Cell corpus DB — `cell-<mno>-<release>-v<N>.db` (SQLite, one file per cell version)

The unit of publish, versioning, and rollback. Immutable after creation
(file-level read-only + never UPDATEd). Contains everything the Ask
surface needs about that cell:

- `meta` — cell key, version, data fingerprint, enrichment run id +
  model, prompt scheme, source build id, created_at. Feeds /healthz and
  StackStamp, replacing MANIFEST.json facts.
- `requirements` — one row per requirement: `req_id` (PK), `doc_id`,
  `plan_id`, `plan_name`, `title`, `text`, `section_number`,
  `parent_section`, `is_requirement`, `hierarchy` metadata. This is the
  union of what `req_tree.find_req` needs (answer bubbles, validation)
  and what SIRA's `corpus.jsonl` carries — one table replaces both.
- `enrichment` — `req_id` → kept enrichment words (from the promoted
  enrichment run). Kept separate from `text` so the raw corpus and the
  enrichment layer stay distinguishable (review, scorecards, re-runs).
- `search` — the BM25 side, **engine-agnostic** (§4): the file carries
  the index as data (postings/df/doclen tables, plus an optional FTS5
  table), and the service picks the engine by config. Committing to the
  file format, not to an engine.

Why one file per cell version, not one big DB: incremental push is a
file copy; immutability is trivial (never reopened for write); rollback
never touches data; concurrent serving of two versions (A/B stacks) is
two open file handles; and a corrupted transfer harms one cell version,
not the store. This keeps today's promote/flip semantics with less
machinery, not more.

**Scale check (global NORA, many MNOs × releases).** Per-cell files
remain the right unit because BM25 statistics must stay per-cell anyway
— scores are not comparable across cells (fusion-layer invariant), so a
merged index would be a ranking bug, not an optimization. Volume: ~20
MNOs × 4 releases/yr × 5 yr retention ≈ 400 cell-version files of tens
of MB — tens of GB total; and the ACTIVE serving set stays small
(latest release or two per MNO, ~20–40 open cells). The registry tiers
the rest: **hot** (active versions, loaded), **warm** (previous version
per cell — the instant rollback target), **cold** (archived by a
retention job; the registry row records where). Schema migrations never
ALTER 400 files: cell DBs are derived artifacts, so a schema bump means
republish/reconvert; each file carries `schema_version` in `meta` and
the loader refuses mismatches loudly. Fallback if ops dislike file
counts later: one DB per MNO with per-release tables — fewer files, but
gives up write-once semantics; held in reserve, not the default.

**Lazy open, LRU close.** The loader opens a cell on first query, not
eagerly from the registry list — a DB-engine open is milliseconds (file
handle + `doclen`/`meta` read) — and closes least-recently-queried
cells past a configured open-cell budget. RAM then tracks active
traffic, not registry size; an MNO nobody queries this week costs
nothing. (Phase 2, with a handful of cells, may eager-load; the
contract matters from phase 4.)

### 2.2 Registry DB — `registry.db` (SQLite, tiny, the only mutable corpus-side state)

- `cell_versions` — cell, version, db filename, fingerprint, published_by,
  published_at, note, eval reference.
- `active` — cell → active version (the flip pointer). Rollback is one
  row update. The promote log's who/when/why lives here.

### 2.3 Runtime DBs (corp)

- `feedback.db` — same schema as today, pooled across stacks. Required
  (scope item 1 names feedback).
- `config.db` — optional, as today (Config page tier).
- `metrics.db` — optional; keep for request/LLM dashboards if wanted.
- Jobs DB — **not deployed** (no pipeline submission in corp).
- LLM roster — unchanged: committed file baked into the image (D-248);
  model discovery queries corp LLM endpoints live, as today.

## 3. The storage seam in code

New protocol (name TBD) with two implementations:

```
CorpusStore
  list_cells() -> [(mno, release, version, fingerprint)]
  cell_meta(cell) -> meta
  iter_requirements(cell) -> rows           # SIRA corpus load, BM25 build
  enrichment(cell) -> {req_id: words}
  find_req(req_id, qualifiers) -> rows      # bubbles, validation
  (+ the narrow queries req_browser/pickers need, dev backend only)
```

- `FsCorpusStore` — wraps today's file reads (`out/parse` trees,
  `corpus.jsonl`, enrichment jsonl). Default; dev/test unchanged.
- `SqliteCorpusStore` — reads cell DBs + registry.
- Consumers changed to go through the seam: `web/req_tree.py` (the only
  web corpus-reader on the Ask path) and sira-query's `_load_one_cell`.
  Everything else (parse review, Eval Studio pickers, req browser) keeps
  reading files and simply does not ship in the corp profile.
- A deploy-profile knob (e.g. `NORA_SURFACE=ask|full`) gates the web
  surface explicitly, so corp does not depend on routes degrading when
  their data dirs happen to be absent.

## 4. BM25 engines — one file format, three engines, eval adjudicates

The cell DB carries the index **as data**, built with `bm25x` itself at
convert time (tokenization, df, doc lengths, enrichment application) so
the stored statistics are parity-true by construction. (Terminology:
the `terms` + `postings` + `doclen` tables together ARE the inverted
index, stored as rows — `terms` is the dictionary, `postings` the
postings lists, per standard IR usage.) The service selects the engine
by config — **exactly one engine per stack, read once at startup; every
query in that process scores through that engine.** No per-query
switching, no fallback chain; two engines only ever run against the
same data across phase-2 A/B stacks, or inside the converter's offline
`--verify` step:

| Engine | What it does | Why it exists |
|---|---|---|
| `memory` | load rows, build the in-memory `bm25x` index at cell load (~seconds, measured) | parity control — identical ranking to today; the baseline every comparison runs against |
| `postings` | read the dumped postings/df/doclen tables from SQLite; scoring loop in the service with `bm25x`'s exact formula, parameters, and max-df rule | the corp-optimized custom option: DB-backed reads, score parity, full control (expansion weighting, future field boosts) |
| `fts5` | FTS5 virtual table over the same token stream; ranking inside SQLite | the zero-custom-code in-engine option; k1/b hardcoded (1.2/0.75), no max-df (emulated via the `df` table at query time), weighted expansion needs a two-query combine |

FTS5's limitations for this corpus are real (fixed parameters,
table-global statistics — neutralized by per-cell files — and no
expansion weighting in a single MATCH), which is why the custom
`postings` engine is a first-class candidate, not a fallback. Rejected
engines: Tantivy (excellent BM25 but a new runtime + non-SQLite index —
weak fit for corp standardization), DuckDB FTS / Postgres pg_search
(extensions, same objection).

Decision mechanism: phase-2 A/B (§5) runs golden + team usage across
engines on identical data; the winner ships. An engine change is an
eval-gated config flip forever after — never a schema change.

**Scale verdict on `memory` (2026-10-09 analysis):** at global scale
(~100 MNOs, ~200 open cells of ~10k requirements) the `memory` engine
costs ~100–150 MB RAM per open cell (~20–30 GB total) plus minutes of
rebuild at startup — disqualified as the production engine. It remains
the eval parity control and debug tool, rebuildable from `corpus` +
`enrichment` rows, which ship regardless of engine. The DB-backed
engines stay flat: <1 MB fixed per open cell plus a configurable page
cache (~2–5 GB total for the whole hot set). Expected winner:
`postings` — exact parity (tuned k1/b, max-df, native weighted
expansion) where fts5 compromises all three; fts5 still runs in the
phase-2 eval (emitting it is nearly free) and ships only if it beats
postings.

**Single-engine publish (phase 4):** the three-engine file is a
phases-1–3 evaluation affordance, not the shipping format. Once the
eval picks the engine, the publish emits `corpus` + `enrichment` + the
winning engine's tables only (~25–30 MB per 10k-req cell). Unused
tables cost disk, not RAM (unread pages never enter the cache), so this
is hygiene, not a memory fix.

## 5. Migration path — four gated phases (architect's plan, 2026-10-09)

Scope note: corp-wide machinery (registry, transfer, profile knob) is
deliberately deferred until the storage layer itself has proven out.

**Phase 1 — converter.** A label-to-cell-DB converter
(`converter-design.md`, this strand) turns the current label's flat
files into cell DBs. Built as the embryo of the phase-4 publisher —
nothing throwaway. No pipeline changes.

**Phase 2 — parallel stack + evaluation.** A second stack (existing A/B
compose mechanics: own project name, env file, ports, state; pooled
feedback) serves the SAME label through the cell DBs; the flat-file
stack keeps serving as-is. nora-web stays on flat files in BOTH stacks,
so the only variable is the retrieval index. Evaluation: golden
Stage-1/Stage-2 against both stack URLs (black-box, zero new eval code;
StackStamps key the runs apart) plus a team-usage round. Acceptance:
`memory`-engine result-set parity with the flat stack (sanity gate),
then per-engine recall@5/@10 not below the flat baseline, Stage-2 judge
no-regression, and per-sample adjudication of misses (a shifted ranking
is not automatically a regression).

**Phase 3 — ingestion flag.** The SIRA-lane build step gains
`--index=fts5` (name per the flag's user-facing intent; it emits the
cell DB). During transition it emits BOTH forms from one build — flat
files and cell DB — so the two stacks always serve byte-identical
corpus+enrichment and the A/B stays honest. Default remains legacy
flat files.

**Phase 4 — full migration + corp.** All cells converted, cell DB
becomes the default build output, the DB-backed stack becomes the
official NORA, and that stack migrates to corp with the publish
pipeline below.

## 5a. Publish pipeline (phase 4 — replaces promote.sh + serve-push.sh for corp)

Dev/test side, after the existing verify + evaluate gates of a cycle:

1. **Build cell DBs** — new `corpus_publish` CLI reads the verified
   build (`out/parse` trees + SIRA cell + kept enrichments), emits
   `cell-<mno>-<release>-v<N>.db` per cell, stamps `meta`, computes the
   fingerprint. Deterministic: same build → same fingerprint. Emits
   `corpus` + `enrichment` + the chosen engine's tables only (§4
   single-engine publish).
2. **Incremental by fingerprint** — cells whose fingerprint matches the
   active corp version are skipped; a quarterly release pushes only its
   new/changed cells.
3. **Stage** — copy files to the corp volume, insert `cell_versions`
   rows (not yet active). Transfer is complete-or-absent per file
   (checksum, temp name + rename — same posture as serve-push).
4. **Gate** — golden Stage-1/Stage-2 run against a corp staging stack
   pointed at the staged versions (black-box over HTTP, unchanged).
5. **Flip** — update `active` rows (one transaction, all cells of the
   release). Healthz verifies reported identity. Rollback = flip back.
6. Old versions GC'd by retention policy, as labels are today.

The dev/test environment keeps promote.sh/serve-flip for its own local
serving, unchanged, until the sqlite backend proves out; then local
serving may adopt the same cell-DB path (optional, later).

## 6. Artifact disposition (complete mapping from artifact-inventory.md)

| Artifact today | Corp-prod | Dev/test |
|---|---|---|
| `out/parse` trees | `requirements` table in cell DBs | unchanged files |
| SIRA `corpus.jsonl` + enrichments | same cell DBs (`requirements` + `enrichment`) | unchanged |
| SIRA `index/best/` | not shipped (rebuild at load; FTS5 optional later) | unchanged |
| Chroma vectorstore | dropped | optional, experiments only |
| Knowledge graph | dropped (tables reserved, later) | unchanged (stage off) |
| `serve/<label>` + MANIFEST + flip | registry.db (`cell_versions` + `active`) | unchanged locally |
| Jobs / metrics / config / feedback SQLite | feedback required; config/metrics optional; jobs absent | unchanged |
| Enrichment overlay, pipeline corrections, annotations | not deployed (dev-only surface; a future MNO onboarding runs them in dev, outputs flow into the next publish) | unchanged |
| Golden samples + runs | not deployed; golden runner targets corp staging over HTTP | unchanged |
| Eval workbooks, reports, parse logs | not deployed | unchanged |
| Roster, config/*.json, profiles, mappings, prompts | git/image, as today | unchanged |

## 7. Invariants carried over (and where they now live)

- **Backward compatibility** → every phase-1–3 change is additive and
  flag-gated with today's behavior as the default (`NORA_SIRA_INDEX`
  defaults to `flat`; the ingestion `--index` flag defaults to legacy
  flat files; `FsCorpusStore` is the default store and must be a pure
  extraction of today's reads, not a rewrite — the phase-2
  memory-engine parity gate is its test). An instance that sets nothing
  runs unchanged. Phase 4 is the only phase that changes a default, and
  it does so by the evaluated new stack becoming official, not by
  flipping flags under existing instances.
- **Label immutability** → cell-DB files are write-once; registry is the
  only mutable corpus state. Enforceable with file permissions.
- **Rollback is repointing** → `active` row flip.
- **Promotion attributable** → registry columns + eval reference.
- **Complete-or-absent transfer** → per-file checksum + rename.
- **Cell isolation** → one DB file per cell; no cross-cell queries.
- **Eval gates promotion** → step 4 of the publish pipeline; also gates
  any BM25-engine change (§4).
- **No proprietary content in logs/reports** → unchanged (D-012); DB
  contains content, surfaces stay clean.
- **Corrections survive labels** → overlay/corrections live in dev and
  reach corp only through the next publish's enriched rows. NOTE the
  semantic change: today an overlay Apply reaches live serving without
  re-promotion; in corp, correction-shaped fixes ride the next publish.
  Acceptable per the scope ruling (surface is dev-only now); flagged so
  it is a decision, not an accident.

## 8. Open points for adjudication

1. ~~BM25 engine choice~~ — superseded: engine-agnostic file + three
   engines, phase-2 eval adjudicates (§4, 2026-10-09).
2. Corp metrics/config DBs: ship or omit at first deployment? (phase 4)
3. Transfer mechanism dev → corp volume (rsync/scp vs an upload
   endpoint on a corp-side admin service): depends what corp network
   allows between the dev PCs and corp storage. (phase 4)
4. `NORA_SURFACE=ask|full` naming and whether team-mode gate subsumes it.
   (phase 4)
5. The §7 overlay-semantics change (corrections ride the next publish in
   corp) — confirm acceptable. For phases 1–3 the converter BAKES the
   current overlay state into the cell DB at convert time; the FTS5
   stack's Apply/pending machinery is inert during the trial (surface
   is dormant anyway).
