# Cell-DB converter design (phase 1)

Strand: `corp-db-layer` · Draft v0.3, 2026-10-09 · Status: implemented (sandbox/cell_db_convert.py)
Companion: `storage-design.md` (§4 engines, §5 phases)
(v0.2, post-review: single engine per process in §6; loader lazy-open
note. v0.3, at implementation: schema amended to blob-primary after
grounding in the real bm25x — see §9 findings. The index ships as the
ENRICHED serialized bm25x engine state, zlib-compressed, not as
textbook postings tables; the SQL `postings` engine is deferred.)

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
| `index/best/` | serialized vanilla `bm25x` index | LOADED (BM25.load + disable_auto_save), enriched in memory, re-serialized into the DB (see §4) |
| `runs/doc-enrich/<run>/enrichments.kept.jsonl` | kept phrases per req + run metadata (enrich model) | → `enrichment` table |
| `enrichments/doc/best.jsonl` | promoted-best fallback phrases | fallback, same precedence as the service |
| label `MANIFEST.json` | label identity, data fingerprint facts | → `meta` (source identity) |
| overlay `<corrections_root>/sira-enrich/` (optional) | expert word-record edits | folded into kept phrases at convert time, main view (baked; digest recorded) |

## 3. Output schema (`schema_version = 1`)

One file per cell: `cell-<mno>-<release>.db`. Built, `VACUUM`ed, then
renamed into place; opened by consumers read-only (`mode=ro`,
`immutable=1`) — write-once is enforced, not assumed.

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version, converter_version, created_at, cell_mno,
-- cell_release, source_label, source_fingerprint (service formula:
-- sha256 over corpus.jsonl + applied phrases file), enrich_run,
-- enrich_model, enriched_docs, overlay_digest_baked, overlay_counts,
-- bm25x_max_n, bm25x_ngrams, n_docs, fts5, index_files (name->sha256)

CREATE TABLE corpus(
  doc     INTEGER PRIMARY KEY,          -- stable load-order index
  req_id  TEXT NOT NULL,                -- corpus.jsonl `_id`; NOT unique —
                                        -- real cells carry duplicate doc/
                                        -- section id rows (field finding
                                        -- 2026-10-10); every row ships,
                                        -- positionally aligned with the
                                        -- index; id lookup is last-wins
                                        -- (max doc), mirroring the service
  title   TEXT NOT NULL DEFAULT '',
  text    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX corpus_req_id_idx ON corpus(req_id);

CREATE TABLE enrichment(
  req_id  TEXT PRIMARY KEY,
  phrases TEXT NOT NULL,                -- JSON array, post-overlay-fold
  run_id  TEXT NOT NULL DEFAULT ''
);

-- the ENRICHED serialized bm25x index, file-per-row
CREATE TABLE index_blob(
  filename TEXT PRIMARY KEY,            -- bm25x serialized file name
  codec    TEXT NOT NULL DEFAULT 'zlib',-- 'zlib' | 'raw'
  data     BLOB NOT NULL                -- codec-encoded file bytes
);

-- optional (--no-fts5 to skip; default on): unigram token stream
CREATE VIRTUAL TABLE fts USING fts5(toks);  -- rowid == corpus.doc
```

Design intents: `corpus` + `enrichment` alone suffice for the `memory`
engine (parity control rebuilds from rows); `index_blob` serves the
`bm25x` engine — extract, `BM25.load`, serve, byte-exact parity with
the flat stack by construction; `fts` is the unigram in-engine
candidate (informational where the source index is multi-gram, §9).
The zlib codec is load-bearing: bm25x's hashed n-gram side serializes
its full slot table (~134 MB of mostly zeros at default `n_features`)
regardless of corpus size; compression stores it at ~500:1 and
decompression restores byte-identical files. The phase-4 publisher
emits only the winning engine's payload (`storage-design.md` §4).

## 4. Conversion procedure (per cell)

1. **Load** `corpus.jsonl` → `corpus` rows (order = file order, so
   `doc` matches the service's historical indexing).
2. **Resolve enrichment** with the service's exact precedence (pinned/
   latest run's `enrichments.kept.jsonl`, else `best.jsonl`, else none).
3. **Fold overlay** (if a corrections root is given): apply the main
   view's word records to the kept phrases using the service-side fold
   semantics; record the overlay digest in `meta`. No overlay → empty
   digest, phrases unchanged.
4. **Enrich the index the way the service does**: `BM25.load` the
   label's `index/best`, `disable_auto_save()` (mutations on a
   path-bound index write back — never touch the label), then
   `enrich_batch` the folded effective sets — the service's exact
   calls, not a re-implementation. Then `save()` the enriched index to
   a temp dir and store each file zlib-compressed in `index_blob`.
   Parity by construction: the DB carries the bytes of the very engine
   state the service would have built.
5. **Emit the FTS5 table** from the unigram token stream: the index's
   own tokenizer over each doc's text plus each effective phrase
   tokenized in isolation (matching bm25x enrich semantics;
   repetitions preserved).
6. **Self-verify** (`--verify N`, default 25): run N probe queries
   (titles sampled evenly across the corpus; expansion = that doc's
   effective phrases, exercising `search_with_expansion`) through
   (a) the in-memory enriched index and (b) a fresh index loaded from
   the DB blobs. Rankings must be IDENTICAL (ids exact, scores within
   1e-6) — any diff fails the cell (CNV-006; undecodable blobs are
   CNV-007). The report carries counts and digests only, never text
   (D-012).
7. **Finalize**: write `meta` (including `source_fingerprint` copied
   from the label, so phase-2 StackStamps show the two stacks serving
   the same data), `VACUUM`, fsync, rename into place.

## 5. CLI

Lives in `sandbox/` (it imports bm25x; core must not — D-111 boundary).

```
python -m sandbox.cell_db_convert
    --label <serve-root>/<label>        # or --db-root <dir> for a raw cell root
    --out <dir>                          # cell DBs land here
    [--cells <mno>__<release>,...]       # default: all cells found
    [--enrich-run <run-name>]            # pin a doc-enrich run
    [--latest-runs]                      # mirrors NORA_SIRA_USE_LATEST_RUNS
    [--overlay-root <corrections-root>]  # bake overlay (main view); default: none
    [--no-fts5] [--verify N] [--force]
```

Output: one `cell-<mno>-<release>.db` per cell, built as a temp file
and renamed into place after verify, plus `conversion-report.json`
(content-free: cells, counts, digests, verify results — chat-pasteable).
Stable error codes (NFR-9): CNV-001 corpus missing · CNV-002 index
missing · CNV-003 pinned enrich run absent · CNV-004 output exists
without --force · CNV-005 enrich_batch failure · CNV-006 self-verify
mismatch · CNV-007 blob round-trip failure · CNV-008 overlay fold
failure · CNV-009 no cells found. Registration in the pipeline catalog
+ the QC template complete the NFR-9 triple before phase 1 is called
shipped.

## 6. Service loader contract (the phase-2 branch)

sira-query gains two knobs, both read at startup:

- `NORA_SIRA_INDEX = flat | cell-db` — where cells come from. `flat`
  is today's path, untouched. `cell-db` enumerates `cell-*.db` under
  `NORA_SIRA_DB_ROOT` instead of cell directories.
- `NORA_SIRA_ENGINE = memory | bm25x | fts5` (cell-db mode only) —
  **exactly one engine per process, read once at startup**; every query
  scores through it. No per-query switching or fallback. Engines only
  meet across phase-2 A/B stacks (via the golden harness) and inside
  this converter's offline `--verify`:
  - `memory`: load `corpus` + `enrichment` rows, rebuild + enrich in
    RAM as `_load_one_cell` does today from files. Parity control.
  - `bm25x` (expected production engine): extract `index_blob` to a
    cell cache dir, `BM25.load`, `disable_auto_save` — byte-exact
    parity with the flat stack, no re-enrichment (the blobs are already
    enriched). Open cost = decompress + load, milliseconds-to-subsecond.
  - `fts5`: two MATCH queries (original-token query, expansion-token
    query, each as quoted-token OR), combined `score = s_orig + w·s_exp`
    in the service; `bm25()` rank is negative-is-better — normalize
    before fusion. Unigram-only: informational where the source index
    is multi-gram (§9).
- Everything above the retrieval call (LLM question enrichment, rerank
  toggle, response shapes, healthz) is unchanged. Healthz reports
  `data_fingerprint = meta.source_fingerprint` plus the engine and
  schema_version, so golden runs key correctly.

## 7. What phase 1–2 deliberately does NOT touch

- nora-web reads (stays on label flat files in both stacks).
- The enrichment-review Apply/pending machinery (inert for cell-db
  cells during the trial; overlay state is baked at convert time).
- Ingestion (`--index=cell-db` emission is phase 3).
- Registry / transfer / corp profile (phase 4; `storage-design.md` §5a).

## 8. Acceptance (exit criteria for phases 1–2)

1. Converter self-verify passes on every cell of the current label
   (DB-blob-loaded index ≡ in-memory enriched index, per cell).
2. Phase-2 `memory`-engine stack returns result sets identical to the
   flat-file stack on the golden Stage-1 queries (sanity gate — proves
   the DB content, load path, and plumbing before any engine question).
3. Per-engine golden recall@5/@10 ≥ flat baseline; Stage-2 judge
   no-regression; per-sample adjudication of any misses.
4. Team-usage round on the cell-db stack raises no quality flags.

## 9. Implementation findings (2026-10-09, grounded in the real bm25x)

Facts discovered at build time that amended v0.2's assumptions; tests in
`sandbox/test_cell_db_convert.py` + `sandbox/sira_query/test_no_autosave.py`
pin them.

1. **bm25x is a Rust engine with a multi-gram tier and hashed feature
   slots** (sira's vendored fork). Scoring sums a unigram tier plus
   hashed n-gram slots (`ngrams` up to `max_n`, chosen per dataset by
   `eval_bm25.py`). A textbook `terms/postings/doclen` dump cannot
   reproduce its ranking; `search_with_expansion` IS a pure sum of
   query-independent per-(key, doc) contributions, so a SQL
   contribution-dump engine remains possible (near-exact — hashed-slot
   collisions) but is DEFERRED: the blob engine gives byte-exact parity
   with zero scoring re-implementation.
2. **Auto-save semantics**: the constructor form `BM25(index=path)`
   auto-saves the whole index after every mutation (enrich included,
   which accumulates tf — no dedup); the static `BM25.load(path)` form
   the service uses does NOT. No live bug — but the service, sira_debug,
   and this converter now call `disable_auto_save()` after every load,
   the same belt-and-suspenders SIRA's own batch scripts use, so a
   future bm25x change or a switch to the constructor form can never
   silently mutate a label.
3. **Serialized size**: the hashed n-gram side serializes its full slot
   table (~134 MB at default `n_features`) regardless of corpus size;
   zlib stores it ~500:1 (50-doc smoke cell: 134 MB raw → 320 KB DB).
   The `codec` column exists for this.
4. **Deployed `max_n` is per-dataset** and not knowable from the dev PC;
   the converter records `bm25x_max_n`/`bm25x_ngrams` in `meta` at
   convert time, which also settles how seriously to take the fts5
   engine for that cell (unigram source index → comparable; multi-gram
   → informational only).
