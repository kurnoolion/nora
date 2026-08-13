## 2026-08-12 — Strand opened; checker + guard + merged-cell HTML implemented

### Done this session
- Strand opened out of the Rover's field report #2, immediately after landing
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
  to the Rover with a count request.
- 12 new tests (5 parser, 5 docx, 2 checker); full suite 2072 passed /
  109 skipped. All commits gated + pushed.
- Pilot handoff #3 sent (validation asks: checker re-run on HTML corpora,
  merged-cell spot-check + struck-run count, unmerged byte-identical check).

### In progress
- Awaiting Rover validation of handoff #3 against the real corpora.

### Next
- Evaluate Rover's struck-run count — if a meaningful fraction of the ~354
  merged tables carry strikes, design a strike-aware HTML path (parser-side
  re-render) instead of the markdown fallback.
- Resolve OBS-1(c): anchored-path empty-text nodes — real gap vs stale-corpus
  ghost (Rover fresh-cell recheck outstanding; carried from handoff #2).
  Real → the anchored path needs inline treatment (this strand); ghost → done.
- Draft decisions at next close once Rover validates (candidates: merged-cell
  HTML render rule incl. strike gate; empty-table guard).

### Flags
- Legacy empty-header `[Table: ...]` inline path still out of scope (cosmetic).
