# Cell-DB converter design (phase 1)

Strand: `corp-db-layer` · Draft v0.2, 2026-10-09 · Status: approved in review
Companion: `storage-design.md` v0.3 (§4 engines, §5 phases)
(v0.2, post-review: single engine per process in §6; loader lazy-open
note; phase-1 emits all engine tables for the eval — the phase-4
publisher emits only the winner's.)

## 1. Job statement

Convert one promoted label's SIRA flat files into one SQLite **cell DB**
per `(MNO, release)` cell, carrying corpus, enrichment, and the BM25
index as data, such that a second sira-query stack can serve from the
cell DBs and be A/B-evaluated against the flat-file stack on identical
content (phase 2). The converter is the embryo of the phase-4 publisher.

**v1 scope: the SIRA serving side only.** nora-web stays on flat files
in both phase-2 stacks (isolates the experiment variable). Parse-tree
fields for nora-web (`find_req` etc.) enter the schema in a later
revision, when the web side migrates.

## 2. Inputs (verified against `sandbox/sira_query/service.py`)

Per cell dir `<label>/sira/<mno>__<release>/`:

| File | Content | Converter use |
|---|---|---|
| `raw/corpus.jsonl` | one row per requirement: `_id`, `title`, `text` | → `corpus` table, byte-faithful |
| `index/best/` | serialized vanilla `bm25x` index | not read — the index is REBUILT (see §4) |
| `runs/doc-enrich/<run>/enrichments.kept.jsonl` | kept phrases per req + run metadata (enrich model) | → `enrichment` table |
| `enrichments/doc/best.jsonl` | promoted-best fallback phrases | fallback, same precedence as the service |
| label `MANIFEST.json` | label identity, data fingerprint facts | → `meta` (source identity) |
| overlay `<corrections_root>/sira-enrich/` (optional) | expert word-record edits | folded into kept phrases at convert time, main view (baked; digest recorded) |

## 3. Output schema (`schema_version = 1`)

One file per cell: `cell-<mno>-<release>.db`. Built, `VACUUM`ed, then
renamed into place; opened by consumers read-only (`mode=ro`,
`immutable=1`) — write-once is enforced, not assumed.

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
-- schema_version, converter_version, created_at,
-- cell_mno, cell_release, source_label, source_fingerprint,
-- enrich_run, enrich_model, overlay_digest_baked,
-- bm25x_version, k1, b, n_docs, avgdl

CREATE TABLE corpus(
  doc     INTEGER PRIMARY KEY,          -- stable load-order index
  req_id  TEXT UNIQUE NOT NULL,         -- corpus.jsonl `_id`
  title   TEXT NOT NULL DEFAULT '',
  text    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE enrichment(
  req_id  TEXT PRIMARY KEY,
  phrases TEXT NOT NULL,                -- JSON array, post-overlay-fold
  run_id  TEXT NOT NULL DEFAULT ''
);

-- the BM25 index as data (dumped from the enriched bm25x index)
CREATE TABLE terms(term TEXT PRIMARY KEY, df INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE postings(
  term TEXT NOT NULL, doc INTEGER NOT NULL, tf INTEGER NOT NULL,
  PRIMARY KEY(term, doc)
) WITHOUT ROWID;
CREATE TABLE doclen(doc INTEGER PRIMARY KEY, len INTEGER NOT NULL);

-- optional (--fts5, default on): same token stream, in-engine ranking
CREATE VIRTUAL TABLE fts USING fts5(toks);  -- rowid == corpus.doc
```

Design intents: `corpus` + `enrichment` alone suffice for the `memory`
engine (parity control rebuilds bm25x from rows); `terms`/`postings`/
`doclen` + meta params serve the `postings` engine; `fts` serves the
`fts5` engine from the identical token stream. The `terms.df` table
also provides query-time max-df emulation for fts5. The phase-1
converter emits ALL engine tables (the phase-2 eval needs them); the
phase-4 publisher will emit only the winning engine's tables
(`storage-design.md` §4 single-engine publish). Unused tables cost
disk, never RAM — unread pages don't enter the cache.

## 4. Conversion procedure (per cell)

1. **Load** `corpus.jsonl` → `corpus` rows (order = file order, so
   `doc` matches the service's historical indexing).
2. **Resolve enrichment** with the service's exact precedence (pinned/
   latest run's `enrichments.kept.jsonl`, else `best.jsonl`, else none).
3. **Fold overlay** (if a corrections root is given): apply the main
   view's word records to the kept phrases using the service-side fold
   semantics; record the overlay digest in `meta`. No overlay → empty
   digest, phrases unchanged.
4. **Build the index the way the service does**: construct the bm25x
   index from the corpus (same build call the batch step uses), then
   `enrich_batch` the folded phrases onto it — NOT by re-implementing
   tokenization or counting. Then **dump** the enriched index's
   postings, df, doc lengths, and parameters (k1, b, avgdl, N) into the
   tables. Parity by construction: the stored statistics are whatever
   bm25x computed.
5. **Emit the FTS5 table** from the same per-doc token stream
   (repetitions preserved, so FTS5's tf matches).
6. **Self-verify** (`--verify N`, default on): run N probe queries
   through (a) the in-memory enriched bm25x index and (b) a scorer over
   the freshly dumped tables. Rankings must be IDENTICAL — any diff
   fails the cell's conversion. FTS5 overlap is reported as
   informational only (it is expected to differ; that is phase 2's
   question). Probe queries: sampled corpus titles + golden-set query
   texts when available; report carries counts and overlap percentages
   only, never text (D-012).
7. **Finalize**: write `meta` (including `source_fingerprint` copied
   from the label, so phase-2 StackStamps show the two stacks serving
   the same data), `VACUUM`, fsync, rename into place.

## 5. CLI

Lives in `sandbox/` (it imports bm25x; core must not — D-111 boundary).

```
python -m sandbox.cell_db_convert
    --label <serve-root>/<label>        # or --db-root <dir> for a raw cell root
    --out <dir>                          # cell DBs land here
    [--cells <mno>__<release>,...]       # default: all cells in the label
    [--enrich-run <run-name>]            # default: service precedence
    [--overlay-root <corrections-root>]  # bake overlay; default: none
    [--no-fts5] [--verify N] [--force]
```

Output: one `cell-<mno>-<release>.db` per cell + a content-free
conversion report (cells, row counts, term counts, verify results,
digests) suitable for chat paste. Error prefix: reuse `SIR-`/new `CNV-`
codes registered in the pipeline catalog (NFR-9 artifact triple:
compact report + QC template + stable codes — required before the
strand calls phase 1 shipped).

## 6. Service loader contract (the phase-2 branch)

sira-query gains two knobs, both read at startup:

- `NORA_SIRA_INDEX = flat | cell-db` — where cells come from. `flat`
  is today's path, untouched. `cell-db` enumerates `cell-*.db` under
  `NORA_SIRA_DB_ROOT` instead of cell directories.
- `NORA_SIRA_ENGINE = memory | postings | fts5` (cell-db mode only) —
  **exactly one engine per process, read once at startup**; every query
  scores through it. No per-query switching or fallback. Engines only
  meet across phase-2 A/B stacks (via the golden harness) and inside
  this converter's offline `--verify`:
  - `memory`: load `corpus` + `enrichment` rows, build bm25x in RAM
    exactly as `_load_one_cell` does today from files. Parity control.
  - `postings`: `CellState` backed by a SQLite scorer — query-side
    expansion DF-filtered against `terms.df` with the same max-df rule
    (`max(1, N × ratio)`), scoring loop implementing
    `search_with_expansion` semantics (original tokens + weighted
    expansion tokens) over fetched postings.
  - `fts5`: two MATCH queries (original-token query, expansion-token
    query, each as quoted-token OR), combined `score = s_orig + w·s_exp`
    in the service; `bm25()` rank is negative-is-better — normalize
    before fusion. Expansion tokens DF-filtered via `terms` first.
- Everything above the retrieval call (LLM question enrichment, rerank
  toggle, response shapes, healthz) is unchanged. Healthz reports
  `data_fingerprint = meta.source_fingerprint` plus the engine and
  schema_version, so golden runs key correctly.

## 7. What phase 1–2 deliberately does NOT touch

- nora-web reads (stays on label flat files in both stacks).
- The enrichment-review Apply/pending machinery (inert for cell-db
  cells during the trial; overlay state is baked at convert time).
- Ingestion (`--index=fts5` emission is phase 3).
- Registry / transfer / corp profile (phase 4; `storage-design.md` §5a).

## 8. Acceptance (exit criteria for phases 1–2)

1. Converter self-verify passes on every cell of the current label
   (postings scorer ≡ bm25x ranking, per cell).
2. Phase-2 `memory`-engine stack returns result sets identical to the
   flat-file stack on the golden Stage-1 queries (sanity gate — proves
   the DB content, load path, and plumbing before any engine question).
3. Per-engine golden recall@5/@10 ≥ flat baseline; Stage-2 judge
   no-regression; per-sample adjudication of any misses.
4. Team-usage round on the cell-db stack raises no quality flags.
