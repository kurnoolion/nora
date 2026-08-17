## D-DRAFT-1 — Headers-only tables render the compact [Table: …] inline; the compact form may span lines

**Context:** Headers-only tables (no data rows) were silently dropped
from node text — nodes looked table-healthy while their cells vanished
from the corpus; field validation surfaced 51 recovered sibling tables
on a single cell. Separately, when header cells contain multi-line
prose the compact render spans lines.

**Decision:** Headers-only tables render as the compact
``[Table: …]`` inline. The compact form is explicitly allowed to span
lines; consumers must not assume one-line.

**Why:** A headers-only table's header cells are often its entire
payload — dropping them is silent content loss, while a full HTML
render for a row-less table is weight without benefit. Stating the
multi-line contract beats letting consumers grow a single-line
assumption the renderer never promised.

**Consequences:** ~50 recovered inlines per large cell at rebuild;
anything parsing compact forms must handle embedded newlines (contract
documented at the renderer).

## D-DRAFT-2 — Merged-cell DOCX tables render lossless HTML at extract time, strike-gated

**Context:** Merged-cell DOCX tables flattened to markdown lose their
rowspan/colspan structure (several hundred merged tables in the field
corpus). But extract-time HTML rendering risks resurrecting content
the parser's profile-gated strike machinery would drop.

**Decision:** The DOCX extractor renders merged tables as lossless
HTML into the block (rowspan/colspan from merged cells, continuation
cells omitted, content escaped), gated on merges present AND no struck
runs; struck merged tables keep the markdown flatten. The parser's
html-preferred inline rule consumes it with no parser change.

**Why:** Merged structure survives into the corpus only via HTML; the
strike gate keeps strike-drop policy where it belongs (parser-side,
profile-gated) instead of being bypassed upstream.

**Consequences:** Merged tables inline as HTML downstream; struck
merged tables intentionally degrade to the flatten; unmerged tables
field-verified byte-identical through the change.

## D-DRAFT-3 — Empty-table guard: contentless TABLE blocks store nothing

**Context:** Layout providers can emit TABLE blocks with neither HTML
nor any non-whitespace grid content. Storing them hung empty TableData
on nodes and appended empty inlines — nodes looked table-bearing while
carrying nothing, and phantom tables could perturb requirement
attachment.

**Decision:** A TABLE block with no html and no non-whitespace grid
cell is skipped entirely: nothing stored, nothing inlined, invisible
to the attachment cursors.

**Why:** An empty table is extraction noise, not content; representing
it misleads every consumer and can break a real table's attachment.

**Consequences:** Rebuilds drop empty-table residue (field-validated:
a tables_field decrease exactly accounting the residue nodes); no
node appears table-bearing without content.

## D-DRAFT-4 — The table checker mirrors the renderer's output classes

**Context:** The inline checker recognized only one render form:
HTML-inlined corpora were false-flagged as missing wholesale, and
compact/anchored forms went uncounted — field triage decomposed one
cell's 158 reported misses into 21 checker-semantics artifacts + 137
real renderer gaps.

**Decision:** The checker counts every render class the pipeline
actually emits — HTML-inlined, compact ``[Table: …]``, and anchored
forms.

**Why:** A checker recognizing fewer forms than the renderer emits
reports false gaps and buries real ones; mirroring the output classes
keeps MISSING an honest signal.

**Consequences:** Checker totals are comparable across corpora
regardless of render path; any new render form must extend the checker
in the same change.
