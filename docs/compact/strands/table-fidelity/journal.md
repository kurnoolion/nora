## 2026-08-12 — Strand opened; checker + guard + merged-cell HTML implemented

### Done this session
- Strand opened out of field report #2, immediately after landing
  mno-b-tables (D-183..185 promoted the same day).
- Checker fix (14db4f4): `has_inline_table()` in sandbox/verify_tables.py now
  accepts provider HTML (`<table`) alongside markdown pipes — layout-provider
  corpora stop false-flagging as MISSING_inline (the bulk of the reported
  "inline regression" was this checker artifact).
- Parser guard (fb6ce7c): a TABLE block with neither html nor any
  non-whitespace grid cell is skipped entirely — no empty TableData stored, no
  empty inline appended, invisible to the leading-id attachment cursors
  (phantom tables can't break a real table's attachment).
- Extractor feature (d6dc45b): merged-cell DOCX tables render lossless HTML
  into block.html (rowspan/colspan from merged_cells, continuation cells
  omitted, content escaped); the parser's html-preferred inline rule picks it
  up with no parser change. Gated on merges present AND no struck runs —
  extract-time HTML would resurrect parser-side profile-gated strike drops
  (D-060); struck merged tables keep the markdown flatten. Deviation flagged
  for real-data validation with a count request.
- 12 new tests (5 parser, 5 docx, 2 checker); full suite 2072 passed /
  109 skipped. All commits gated + pushed.
- Validation handoff sent (asks: checker re-run on HTML corpora,
  merged-cell spot-check + struck-run count, unmerged byte-identical check).

### In progress
- Awaiting validation of the handoff against the real corpora.

### Next
- Evaluate the struck-run count from validation — if a meaningful fraction of the ~354
  merged tables carry strikes, design a strike-aware HTML path (parser-side
  re-render) instead of the markdown fallback.
- Resolve OBS-1(c): anchored-path empty-text nodes — real gap vs stale-corpus
  ghost (fresh-cell recheck outstanding; carried from handoff #2).
  Real → the anchored path needs inline treatment (this strand); ghost → done.
- Draft decisions at next close once validation lands (candidates: merged-cell
  HTML render rule incl. strike gate; empty-table guard).

### Flags
- Legacy empty-header `[Table: ...]` inline path still out of scope (cosmetic).

## 2026-08-16 — Catch-up: round-2 validation + field close-out (two sessions)

### Done this session
- (2026-08-13) Round-2 validation accepted: the 21+137 decomposition
  (checker semantics vs renderer fix) confirmed to the digit; 106
  formerly-empty nodes now carry their table content as compact inlines;
  byte-level diff vs round-1 trees additions-only (zero removals, zero
  modifications), with 51 headers-only sibling tables recovered that
  were silently dropped even on table-healthy nodes; multi-line
  compact-form contract note shipped.
- (2026-08-14) Field close-out: MISSING_inline 0 on every cell
  (mno-a REL-2 134→0; mno-c 12→0 and 11→0; mno-b 0 throughout);
  the tables_field −23 delta exactly accounts the empty-table residue
  dropped by the guard on rebuild; re-enrichment executed the
  anticipated plan-stamp backfill (~9200 evictions on mno-a REL-1 —
  its enrichment carries taxonomy context for the first time); mutated
  serve-labels written off (the writer-class fix is canonical as D-190).

### In progress
- (none — field validation complete)

### Next
- /land-strand table-fidelity: promote D-DRAFT-1..4.

### Flags
- Coverage boundary parked: id-less table-carrying reqs are excluded
  from the corpus by design — candidate scope for a future
  corpus-coverage strand.
