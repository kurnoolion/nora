## 2026-08-12 — Band-swallow + leading-id attachment fixes; field-validation loop established

### Done this session
- Established the field-validation loop: redaction-safe field reports from
  the runtime environment drive fix scoping; placeholder conventions for
  those reports documented.
- Extraction fix (30c3230): `_drop_background_rects()` filters fill-only,
  stroke-less rects/curves covering >= 50% of page area before `find_tables()`,
  preventing background bands from inflating table bboxes and swallowing body
  text. Identity no-op on band-less pages. 10 new tests (incl. synthetic-PDF
  pair pinning pdfplumber inflate/hug behavior).
- Parser fix (a035a10): TABLE/IMAGE blocks attach to
  `current_leading_req or current_section` in leading-id mode with a
  fresh-heading guard, mirroring the paragraph branch; heading mode unchanged.
  9 new tests.
- Full suite 1676 passed / 109 skipped. Both fixes validated against the
  real mno-b corpus: swallowed requirements reappeared, tables attach to their
  requirements.
- Docling evaluated on sample mno-b pages: no fidelity gain over
  geometric tables — mno-b keeps its current profile (D-DRAFT-3).
- Reviewed field report #2 (verify_tables checker false alarm, mno-a
  merged-cell fidelity plan); review verdicts sent back.

### In progress
- (nothing — strand scope is complete pending land)

### Next
- Land this strand (3 draft decisions pending promotion).
- Open a follow-up strand for table fidelity: verify_tables HTML-aware checker,
  empty-table parser guard, mno-a merged-cell HTML render (docx_extractor).
- Await recheck of the mno-a empty-text-node claim on a freshly built
  cell (suspected stale corpus; contingent input to the new strand's scope).

### Flags
- Out of scope, unowned: legacy `[Table: ...]` empty-header inline path
  (cosmetic MISSING in verify_tables).
