## D-DRAFT-1 — Extractor drops giant fill-only background rects/curves before table detection

**Context:** mno-b's single-PDF corpus paints pages with full-width fill-only,
stroke-less background bands (header band, large content band, footer band).
pdfplumber's "lines" table strategy treats rect edges as table rules; when a
real ruled table shares its x-extent with a band, the band edges join the
table lattice, the detected bbox inflates to the band, and the extractor's
committed-bbox text suppression swallows every paragraph on the page into
phantom table rows — requirements silently vanish from the IR.

**Decision:** `_drop_background_rects()` in `core/src/extraction/pdf_extractor.py`
filters page objects before `find_tables()`: any object with
`object_type in ("rect", "curve")`, a fill, no stroke, and area >= 50% of the
page area (`_BG_RECT_PAGE_FRAC = 0.5`) is removed. When a page has no such
object the original page is returned unfiltered (identity no-op), so corpora
without bands take the exact pre-fix code path.

**Why:** A page-fraction threshold on fill-only/stroke-less objects targets
decorative paint precisely: real table cells are stroked and small; no
legitimate table rule is a giant unstroked filled shape. The "curve" arm is
included because pdfminer classifies the identical fill-only band as a curve
(not rect) when the producer paints it as a closed line path instead of a
`re` operator — both encodings feed edges to the lattice. Rejected: per-profile
pdfplumber `table_settings` tuning (would fight the symptom per corpus instead
of removing the cause, and risks degrading genuine table detection).

**Consequences:** Table detection sees a filtered object set on banded pages;
any future giant filled shape that IS meaningful (e.g. a full-page table with
filled background and no strokes) would be filtered — accepted as implausible
for ruled-table corpora. Tests pin pdfplumber's current inflate/hug behavior
on a synthetic banded PDF, so a pdfplumber upgrade that changes lattice
construction will surface as a test failure prompting re-evaluation.

## D-DRAFT-2 — Leading-id mode: TABLE/IMAGE blocks attach to the preceding requirement

**Context:** In `leading_id_body` detection mode (mno-b's shape — requirements
are flat body paragraphs opening with a req_id under non-requirement
headings), the parser's TABLE and IMAGE branches attached only to
`current_section`. Section nodes carry no req_id and downstream chunk
consumers drop id-less nodes, so tables were "in the tree" but absent from
the corpus — the parser-side half of the mno-b table loss.

**Decision:** In leading-id mode, TABLE and IMAGE blocks attach to
`current_leading_req or current_section`, mirroring the paragraph-continuation
branch, with a fresh-heading guard: a table/image arriving immediately after
a heading attaches to the heading's section and clears the stale requirement
cursor. Two boundaries hold: (a) deferred table-anchored extraction keeps the
SECTION as parent even in leading-id mode (table-anchored reqs parent into
the section hierarchy, not onto a sibling requirement); (b) the guard only
reads `previous_block_was_heading` — the flag is never modified in the
TABLE/IMAGE branches, so heading-continuation merging is byte-identical in
both modes. Heading mode is unchanged.

**Why:** A table following a requirement paragraph is that requirement's
content (bearer tables, config matrices) — the same document-order logic the
paragraph branch already used; asymmetry between the branches was the bug.
The fresh-heading guard prevents a section-preamble table from being claimed
by the last requirement of the previous section. Deviations (a)/(b) from the
field fix plan keep the section hierarchy and heading-merge invariants stable;
both were validated by the Rover against the real corpus.

**Consequences:** Requirement nodes in leading-id corpora now carry `tables`
/ `images` and their inlined text; consumers see table content under the
req_id it belongs to. Extends the D-DRAFT-2 (golden-eval strand) leading-id
contract — parser MODULE.md invariants need updating at land time.

## D-DRAFT-3 — mno-b keeps geometric table extraction; Docling evaluated and rejected

**Context:** mno-c ingests via the Docling layout provider
(`layout_provider: "docling"`), which yields lossless HTML tables. After the
mno-b band/attachment fixes, the natural question was whether mno-b (and
mno-a) should adopt the same recipe.

**Decision:** mno-b stays on geometric (pdfplumber) table extraction — no
layout provider. Decided on field evidence: Docling was run on a sample page
range of the real mno-b document and showed no fidelity improvement over the
geometric tables.

**Why:** mno-b's tables are ruled/lattice tables that the geometric path
(post band-filter) captures faithfully; Docling adds nothing to correct
output. The cost side is real: a several-hundred-page PDF through a CPU
layout model turns a minutes-long extract into hours and forces a full cell
rebuild + re-enrichment. No fidelity gain at that cost is a clear reject.

**Consequences:** mno-b's profile keeps geometric extraction; the band filter
(D-DRAFT-1) remains load-bearing for it (a layout provider would have
sidestepped bands differently). Revisit trigger: a future mno-b document
release with borderless or merged-cell tables the geometric path can't
represent.
