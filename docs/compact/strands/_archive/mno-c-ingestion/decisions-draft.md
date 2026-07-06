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

## D-DRAFT-4 — Broadened req-id capture + `is_requirement` type discriminator

**Context.** MNO-C structural sections carry their own ids (a section-type tag,
`<PREFIX2>`) alongside actual requirements (`<PREFIX3>`). The original pattern was
scoped to the requirement type only, so sections got empty `req_id` — breaking
parent linking and hiding section identity. But simply broadening the pattern
would make every downstream consumer (`if c.req_id` sites, SIRA per-req corpus)
treat sections as requirements.

**Decision.** Broaden `requirement_id.pattern` to a non-capturing alternation
over both type tags, and add `requirement_id.requirement_type_pattern` (narrow,
requirement tag only): a post-link parser pass sets
`Requirement.is_requirement = bool(req_id matches the narrow pattern)`. The flag
is serialized to tree JSON (back-compat load default `bool(req_id)`), carried
into chunk metadata by the vectorstore, and honored by the SIRA adapter (nodes
with `is_requirement=False` are excluded from per-req corpus rows and the
multigranularity pointer lists; they still feed Context via `section_index`).

**Why.** Sections need ids for linking/context; requirements need to stay the
only retrievable/citable unit. A single broadened pattern + a narrow
discriminator keeps both, profile-only (D-003), with legacy corpora unchanged
via the empty-pattern/back-compat default. Alternative — separate section-id
field — rejected: every consumer of `req_id` would need a parallel field.

**Consequences.** Every new profile regex field MUST be added to
`substitute_placeholders` (this one initially wasn't → every node
`is_requirement=False`, 0 SIRA corpus rows) and re-runs must start from the
`profile` stage (substituted profile is cached). Query-side `is_requirement`
gating (synthesizer/query_cli/context_builder `if c.req_id` sites) is deferred
until the NORA-native lane is exercised.

## D-DRAFT-5 — Definitions are `ID:`-labeled; bare req-ids are references

**Context.** MNO-C requirement headings end with `(PRIORITY) ID: <REQ-ID>`, but
release-notes/change-log sections cite the same req-ids in headings and body
text. The parser's three req-id paths (heading inline, standalone small-font
block, body-text scavenge) all used broad `findall`-style matching, so citations
became duplicate requirement nodes (90 real-requirement duplicates in the MNO-C
corpus).

**Decision.** New `requirement_id.id_label_pattern`: when set, a node's OWN
req_id is captured ONLY from the `ID:`-labeled marker (group 1) — overriding
`anchor` and gating all three id paths; a bare req-id anywhere is a reference.
For the residual case a citation carries the `ID:` label too (indistinguishable
by pattern), `exclude_section_pattern` gains `release notes`: the section is
title-matched (its number varies per doc) and its whole subtree is dropped
pre-serialization.

**Why.** The label is the corpus's own definition marker — position-based
alternatives failed in testing (an end-of-line anchor was tried and dropped:
citations are bare, so label presence is the sole reliable discriminator, and
the anchor only risked missing definitions). Whole-section exclusion beats
pattern tweaks for `ID:`-cited citations because no lexical signal separates
them from definitions. Result: requirement-duplicates 90 → 0, verified on the
full MNO-C corpus.

**Consequences.** Corpora with `id_label_pattern` set get NO req_id on unlabeled
headings (strict by design). The Release Notes content is absent from the tree
and all retrieval surfaces. Empty pattern = legacy anchor behavior, so other
corpora are untouched.
