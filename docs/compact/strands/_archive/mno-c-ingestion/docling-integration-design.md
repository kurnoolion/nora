# Docling layout-provider integration — design sketch

Status: phase 1 implemented (2026-07 — protocol + DoclingProvider + IR fields +
fusion + MNO-C profile opt-in shipped; spike validated in
`experiments/layout-bakeoff/`). Strand: mno-c-ingestion.

> Implementation note: the provider files landed **flat** in `core/src/extraction/`
> (`layout_provider.py`, `docling_provider.py`) rather than the `extraction/layout/`
> subpackage sketched below; `available()` reports a generic missing-dep reason, with
> offline provisioning documented in the module docstring. The extractor also grew a
> `provider_table_grid` toggle (skip the flat headers/rows grid when table-anchored
> extraction is off — HTML is the lossless source). Otherwise as designed.

## Goal

Route **tables and figures** through Docling (bake-off result: HTML tables match
source exactly; figure crops captured), while keeping the deterministic,
profile-driven parser for **requirement text / headings / req-ids / applicability
/ strikethrough**. Per-corpus opt-in — MNO-A/B keep the current pdfplumber path.

## Load-bearing principle

Docling supplies **structure** (tables, figures) as `ContentBlock`s inside the
**existing `DocumentIR`**; pymupdf keeps supplying **text** (paragraph blocks
*with per-span fonts*, which the profiler's heading-detection depends on — Docling
does not expose those). The structural parser stays essentially unchanged: it
already attaches `TABLE` / `IMAGE` blocks to the enclosing Requirement by document
position.

> We do NOT adopt Docling's text/OCR path — that would lose born-digital text
> exactness, font metadata, and determinism. Docling = tables + figures only.

## Architecture (extract stage, per PDF, profile-gated)

```
pymupdf   ->  PARAGRAPH blocks (text + fonts)                 [unchanged]
Docling   ->  TABLE blocks (HTML) + FIGURE blocks (crop+caption)
              each carrying page + bbox
fuse      ->  drop pymupdf paragraphs overlapping a Docling table/figure bbox
              (reuses _overlaps_any_table), merge all blocks, sort by (page, y)
          ->  DocumentIR
profile / parse / graph / vectorstore                         [unchanged flow]
```

So Docling **replaces** the extractor's pdfplumber table detection and
`get_images()` figure extraction; everything downstream is fed the same IR shape.

## LayoutProvider protocol (promote spike -> core)

- New concern `core/src/extraction/layout/`:
  - `layout_provider.py` — the protocol + normalized result types (promoted from
    `experiments/layout-bakeoff/layout_provider.py`; mirrors `LLMProvider` etc.).
  - `docling_provider.py` — `DoclingProvider`: run Docling (OCR off,
    table-structure on, picture-images on), map its tables/figures to normalized
    blocks, normalize bbox to the extractor's top-left PDF-points convention.
- `available()` gates a missing dep / missing models with a fail-loud message
  pointing at the offline provisioning (`DOCLING_ARTIFACTS` + `HF_HUB_OFFLINE`).

## Changes by module

1. **models/document.py** — `ContentBlock`: add `html: str = ""` (lossless table
   HTML — keeps merged cells) and a `caption: str = ""` for figures (image_path +
   surrounding_text already exist). `TableData`: add `html`. Additive, back-compat.
2. **extraction/layout/** (NEW) — protocol + `DoclingProvider` + a pure
   `bbox`-normalization helper.
3. **extraction/pdf_extractor.py** — when the profile selects a layout provider:
   skip pdfplumber table detection + `get_images`; call the provider; convert its
   tables -> `TABLE` blocks (with `html`), figures -> `IMAGE` blocks (image_path +
   caption); add their bboxes to `table_bboxes` so the pymupdf paragraph pass
   suppresses the underlying text (existing mechanism). Keep the paragraph pass.
4. **profiler/profile_schema.py** — `DocumentProfile.layout_provider: str = ""`
   (`""` = current pdfplumber path; `"docling"` = Docling). Per-cell, exactly like
   `detect_text_tables` / `header_footer_margin_mode`.
5. **pipeline/stages.py** — resolve `layout_provider` per cell in
   `_cell_extract_hints`, thread through `extract_document`.
6. **parser/structural_parser.py** — a `TABLE` block that has `html` inlines the
   HTML (or a markdown conversion) at document position instead of
   `render_table_markdown(headers, rows)`; carry `html` into `TableData`. Figures
   already flow via `ImageRef`. Minimal change.

## The real integration detail: coordinate normalization

Docling's bbox has a `coordinate_origin` (TOPLEFT / BOTTOMLEFT) and page size;
convert to the extractor's top-left PDF-points convention (via page height) so
fusion (paragraph suppression) and the `(page, y)` reading-order sort line up with
pymupdf geometry. One helper, unit-tested against both origins.

## Dependency & deployment

- Docling is **heavy** (torch + layout/TableFormer models). Make it an **optional
  extra**, lazy-imported; a profile selecting `docling` without it installed
  fails loud with guidance. NORA stays installable without Docling for corpora
  that don't need it.
- **Offline/proxied deploys**: bundle the two model repos (layout-heron @ main,
  docling-models @ v2.3.0) and set `DOCLING_ARTIFACTS` + `HF_HUB_OFFLINE=1`
  (already solved in the spike; `fetch_docling_models.py`).
- Per-cell opt-in keeps the cost where it's needed.

## Testing

- Unit tests use a **FakeLayoutProvider** returning canned tables/figures — no
  real Docling, no models, fast + deterministic. Assert: paragraph suppression
  under a table/figure bbox, block ordering, `html` carried into `TableData`,
  figure -> `IMAGE` block with image_path + caption.
- Bbox-normalization helper: pure unit test (both coordinate origins).
- One optional integration smoke test, gated on an env flag + models present.

## Open questions / risks

- **Docling vs pdfplumber coverage**: replace outright, or run both and dedup by
  bbox (belt-and-suspenders for tables Docling might miss)? Start replace-only for
  selected cells; revisit if misses appear.
- **HTML tables downstream**: keep HTML lossless; flatten to headers/rows only if
  a consumer needs it. Decide chunking/embedding for large HTML tables.
- **Performance**: Docling runs layout inference per page — measure on the corpus;
  per-cell opt-in bounds the blast radius.
- **Figures for RAG**: the crop is saved now; a later step vision-captions it for
  retrieval — ties into `asset-ingestion-design.md`.

## Rollout phases

1. Protocol + IR `html`/`caption` fields + `DoclingProvider` (tables+figures) +
   `layout_provider` profile flag + parser HTML handling. MNO-C only, behind the
   flag. Fake-provider unit tests.
2. Validate on the MNO-C corpus (tables + figures) vs current output; tune;
   measure performance.
3. Extend to figure / API-spec **asset ingestion** (vision captions) — the
   deferred work.

## Decision to record at land time (draft D-DRAFT)

> Adopt a `LayoutProvider` abstraction and use **Docling for table + figure
> structure** on opt-in corpora, while keeping the **pymupdf text layer + the
> profile-driven structural parser** for requirement text. Rejected: full
> Docling / VLM-OCR document parsing (loses born-digital text exactness, font
> metadata for heading detection, and determinism/auditability); rejected:
> Hiro-Smart-Doc as the engine (immature, patent-domain-tuned, heavy GPU service).
> `LayoutBlock` maps onto `ContentBlock`; the protocol mirrors
> `LLMProvider`/`EmbeddingProvider`/`VectorStoreProvider`.
