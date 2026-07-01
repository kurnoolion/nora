# Draft decisions — mno-c-ingestion

Promoted to canonical DECISIONS.md (with real D-XXX ids) at `/land-strand` time.

---

## D-DRAFT-1 — Adopt a LayoutProvider abstraction; use Docling for table + figure structure

**Context.** MNO-C tables (bordered and borderless, with merged cells and
multi-line/ragged content) were not reliably extracted by the geometric
pdfplumber + custom borderless detection, and downstream RAG/compliance needs
faithful tables; deferred work also needs figures / API-spec parsing.

**Decision.** Introduce a `LayoutProvider` protocol (mirrors `LLMProvider` /
`EmbeddingProvider` / `VectorStoreProvider`) that returns a document's tables +
figures; select it per-corpus via `DocumentProfile.layout_provider` (default `""`
= built-in geometric path). Implement `DoclingProvider`. Keep the pymupdf text
layer + the profile-driven structural parser for requirement text / headings /
req-ids. Fuse provider tables/figures into the existing `DocumentIR` by position:
bboxes normalized to top-left points, the underlying pymupdf paragraphs
suppressed, blocks sorted into reading order.

**Why.** Docling's layout model + TableFormer handle bordered and borderless
tables and figures far better than geometric heuristics (bake-off: HTML tables
matched source exactly; figures captured). A protocol keeps it vendor-neutral,
optional, and profile-gated. Rejected full Docling / VLM-OCR document parsing
(loses born-digital text exactness, the font metadata the profiler's heading
detection needs, and determinism/auditability). Rejected Hiro-Smart-Doc as the
engine (immature — 4 commits / 13 stars / no release; patent-domain-tuned; heavy
GPU vLLM service).

**Consequences.** Docling is an optional heavy dependency (not in
`requirements.txt`); a profile selecting it fails loud without Docling installed +
models provisioned offline (`DOCLING_ARTIFACTS` / `HF_HUB_OFFLINE`). No per-table
geometric fallback when a provider is set — Docling recall is trusted (hybrid
Docling + pdfplumber-lines dedup is the documented fallback if misses appear). New
public surface: `extraction.layout_provider` / `docling_provider`, `PDFExtractor`
layout params + fusion, `models.ContentBlock.html`/`.caption`,
`DocumentProfile.layout_provider`, `TableData.html` — MODULE.md curation pending.
Design detail in `docling-integration-design.md`.

---

## D-DRAFT-2 — Borderless tables: preserve as demarcated text, not a reconstructed grid

**Context.** MNO-C borderless tables (no ruling lines, wrapped multi-line cells,
ragged columns) could not be reconstructed into a clean row/column grid. Three
attempts each mangled real tables: whole-page pdfplumber text strategy (mis-read
prose as a grid), numbered-row region detection (too narrow), and explicit
column-line grids (row explosion, separators inside words, header split off).

**Decision.** In the geometric path, detect a borderless-table region via
persistent column gutters and emit it as a layout-preserved `[TABLE]…[/TABLE]`
TEXT block at its document position — no forced row/column grid.

**Why.** A mangled grid reads worse than clean text for RAG / LLM synthesis; the
original problem (table content merging into the requirement's prose and losing
its identity) is solved by demarcation, which doesn't require a correct grid.
Grid reconstruction of wrapped/ragged borderless tables proved unreliable and
un-validatable without the source PDF.

**Consequences.** Borderless tables in the geometric path have no formal column
structure (acceptable for RAG). Superseded for MNO-C by the Docling path
(D-DRAFT-1) but retained as the built-in behavior for corpora without a layout
provider. The font-gate (`require_heading_font_for_numbering`) remains the safety
net against an undetected borderless table becoming false sections.

---

## D-DRAFT-3 — Profile-gated header/footer margin handling (`pattern_only`)

**Context.** The PDF extractor blanket-drops all text in the top ~65pt /
bottom ~50pt as header/footer. On MNO-C, requirement headings that begin at the
very top of a page were silently discarded (lost requirements across many pages).

**Decision.** Add `DocumentProfile.header_footer_margin_mode`: `"blanket"`
(default, unchanged) vs `"pattern_only"` (margin text is kept and dropped only if
it matches a repeating-header / "Page N of M" / confidential pattern). MNO-C set
to `pattern_only`.

**Why.** A blanket margin drop is too aggressive for corpora where requirements
start high on the page; per-corpus gating avoids regressing OA, which relies on
the blanket drop. Rejected simply shrinking the margin — position alone can't
distinguish a header at y≈40 from a requirement at y≈36 on the same page.

**Consequences.** In `pattern_only`, a non-repeating margin artifact (e.g. a
unique top-right date) may leak into the IR as a stray block (extend the patterns
as needed). OA and other corpora are unaffected on the default `blanket`.
