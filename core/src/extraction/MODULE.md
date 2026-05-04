# extraction

**Purpose**
Format-aware content extraction. Each format has its own extractor (PDF via pymupdf + pdfplumber, DOCX via python-docx, XLSX via openpyxl), all producing the normalized `DocumentIR` defined in [models](../models/MODULE.md). Downstream stages (profiler, parser) treat every document uniformly after this boundary. Serves FR-1 (PDF + DOCX + XLSX extraction), FR-30 (sources read from `<env_dir>/input/<MNO>/<release>/`), and FR-33 (strikeout detection); implements D-010 (multi-format DocumentIR), D-018 (DOC/XLS deferred per FR-27), D-023 (input path layout), D-031 (strikeout detection per format).

**Public surface**
- `BaseExtractor` (base.py) — ABC: `extract(file_path, mno="", release="", doc_type="") -> DocumentIR`
- `PDFExtractor` (pdf_extractor.py) — text blocks with `FontInfo` (incl. strikethrough via PyMuPDF span flags bit 8), tables via pdfplumber, images; header/footer margin filtering
- `DOCXExtractor` (docx_extractor.py) — paragraphs (with style/level + strikethrough from `Run.font.strike`), tables, embedded images
- `XLSXExtractor` (xlsx_extractor.py) — per-sheet extraction via openpyxl: each non-empty worksheet emits a heading (sheet title) + a table block; cell strikethrough surfaced via `Cell.font.strike` for row-level drop semantics. Page numbers track sheet index (1-based).
- Registry (registry.py):
  - `supported_extensions() -> set[str]`
  - `get_extractor(file_path) -> BaseExtractor` — extension-keyed lookup; raises `ValueError` on unsupported
  - `extract_document(file_path, mno, release, doc_type) -> DocumentIR`
  - `infer_metadata_from_path(file_path) -> {mno, release, doc_type}` — walks the `<env_dir>/input/<MNO>/<release>/` convention (D-023); `doc_type` defaults to `"requirement"` (FR-26 deferred)
- `extract.main()` — CLI entrypoint (`python -m core.src.extraction.extract`)

**Invariants**
- Every extractor returns a valid `DocumentIR`; extractor-specific details never leak into the IR's type surface.
- Text blocks from PDF **must** carry `FontInfo` — the profiler's heading detection clusters blocks by font size/boldness and will degrade silently if this is missing.
- Block `Position.index` is a contiguous sequence starting at 0 across the whole document, reflecting reading order.
- Tables extracted by pdfplumber are de-duplicated against text blocks they overlap with (PDF text extractors surface table cells as text too) — no block should appear twice in the IR.
- Header/footer content (matched by margin thresholds + always-header regex patterns) is dropped, not emitted as paragraphs.
- Format-specific libraries (fitz, pdfplumber, python-docx, openpyxl) are imported **only** inside this module — no other `core/src/` module pulls them in.
- Strikethrough block-level signal differs per format AND block type [D-031, D-036]:
  - **PDF paragraph** — majority-of-characters across mixed-strike spans (50% defaults to `False`).
  - **PDF table (whole-table strike)** — `_table_is_struck` counts horizontal strike lines crossing ≥50% of the table width AND not aligned with a `Table.rows[*].bbox` edge (within `edge_tol=1.5pt`). Row-edge filter is critical: pdfplumber draws each row boundary as a full-width horizontal line which the unfiltered heuristic counted as a strike (D-036, addresses 93% false-positive rate observed pre-filter).
  - **PDF table (per-row cell strike)** — `_detect_struck_rows` flags rows whose interior (`y_top + 1.5 < y < y_bot - 1.5`) contains ≥1 horizontal strike line. Header row (index 0) is never marked struck — telecom tables retain their header even when all data rows are deleted.
  - **DOCX** — `any` run struck → whole paragraph struck.
  - **XLSX** — row carries strikethrough only when **all** non-empty cells are struck.

**Key choices**
- PDF: pymupdf (fitz) for text + font metadata, pdfplumber for tables — neither alone covers both well. Pay the double-parse cost per file; cache is the IR JSON on disk.
- Font groups within a single text span are split into sub-blocks when they diverge — preserves heading detection on pages that mix body and heading fonts on one line.
- Header/footer detection uses vertical margin thresholds (`HEADER_MARGIN_PT=65`, `FOOTER_MARGIN_PT=50`) plus a regex allow-list of phrases that are always header/footer regardless of position.
- Registry is an instance dict (`_EXTRACTORS`), not a class hierarchy — extractors are stateless; one instance per format.
- Path-based metadata inference (`<env_dir>/input/<MNO>/<release>/file.ext` per D-023) avoids hardcoding per-MNO dispatch; a new MNO needs no code change.
- XLSX strategy is one heading + one table per worksheet — minimal but lets the profiler still cluster headings by font size and the parser still see structured rows.
- Strikeout detected per format. Whole-table and paragraph strikes are marked via `font_info.strikethrough` and dropped by the parser (the corrections workflow can override `profile.ignore_strikeout` without re-extracting) [D-031]. **Exception**: PDF table rows with cell-level strikes (per-word strike segments inside specific cells, common in OA cross-reference tables) are dropped at extract time from the table's `rows` list — the IR has no per-row strike state to preserve and the alternative (mark + parser-side drop) would require a schema extension. If all data rows drop, the table is marked `strikethrough=True` so the parser drops the now-empty remnant via the existing FR-33 path [D-036]. DOCX/XLSX continue to mark, not drop.

**Non-goals**
- No OCR — scanned PDFs without a text layer will yield empty IRs; this is surfaced via block_count=0, not silently filled by an image-to-text model.
- No semantic interpretation (heading levels, requirement IDs) — that is the profiler + parser's job.
- No DOC (binary Word 97) or XLS support — DOC requires conversion to DOCX first; both are deferred per FR-27 / D-018 (revisit when a corpus needs them). XLSX is in scope per FR-1.

<!-- BEGIN:STRUCTURE -->
_Alphabetical, regenerated by regen-map._


`base.py`
- `BaseExtractor` — class — pub — Abstract base class for document content extractors.
  - `extract` — method — pub — Extract content from a document file.

`docx_extractor.py`
- `_BODY_DEFAULT_SIZE` — constant — internal
- `_HEADING_DEFAULT_SIZE` — constant — internal
- `_HEADING_STYLE_RE` — constant — internal
- `DOCXExtractor` — class — pub — Extract paragraphs, tables, and images from DOCX files.
  - `_count_leading_page_breaks` — staticmethod — internal — Count page breaks in the paragraph before the first run that emits text.
  - `_count_trailing_page_breaks` — method — internal — Total page breaks in paragraph minus ones already counted as leading.
  - `_extract_paragraph_images` — method — internal
  - `_heading_level` — staticmethod — internal
  - `_paragraph_block` — method — internal
  - `_paragraph_font` — method — internal — Synthesize a FontInfo from the first run with real font data.
  - `_style_font_size` — staticmethod — internal — Walk the style inheritance chain for an explicit font size.
  - `_surrounding_text` — staticmethod — internal
  - `_table_block` — method — internal
  - `extract` — method — pub

`extract.py`
- `extract_file` — function — pub — Extract a single file and save the result as JSON.
- `main` — function — pub

`pdf_extractor.py`
- `PDFExtractor` — class — pub — Extract text blocks, tables, and images from PDF files.
  - `_bboxes_overlap` — staticmethod — internal — Check if bbox A overlaps with bbox B by more than threshold of A's area.
  - `_block_to_text` — staticmethod — internal — Extract plain text from a pymupdf block dict.
  - `_collect_strike_lines` — staticmethod — internal — Collect candidate strike-through line segments on a page (FR-33 [D-031]).
  - `_detect_header_footer_patterns` — method — internal — Detect text that repeats across most pages (headers/footers).
  - `_detect_struck_rows` — staticmethod — internal — Return data-row indices (0-based, header excluded) whose
  - `_extract_impl` — method — internal
  - `_extract_text_segments` — method — internal — Extract text segments from a pymupdf text block, preserving font info.
  - `_get_surrounding_text` — staticmethod — internal — Get text from the most recent paragraph blocks on the same page.
  - `_group_by_font` — method — internal — Group contiguous segments with similar font size into blocks.
  - `_is_in_margin` — method — internal — Check if a block is in the header or footer margin.
  - `_make_group` — staticmethod — internal — Create a font group from collected segments.
  - `_matches_header_footer` — method — internal — Check if text matches a detected header/footer pattern.
  - `_overlaps_any_table` — method — internal — Check if a text block overlaps with any detected table region.
  - `_span_struck` — staticmethod — internal — Check whether any strike line meaningfully crosses the span.
  - `_table_is_struck` — staticmethod — internal — Decide whether a table block is struck through (FR-33 [D-031]).
  - `extract` — method — pub

`registry.py`
- `_EXTRACTORS` — constant — internal
- `extract_document` — function — pub — Extract a document using the appropriate format extractor.
- `get_extractor` — function — pub — Get the appropriate extractor for a file based on its extension.
- `infer_metadata_from_path` — function — pub — Infer mno and release from folder structure (D-023, FR-30).
- `supported_extensions` — function — pub — Return the set of file extensions with registered extractors.

`xlsx_extractor.py`
- `_BODY_FONT_SIZE` — constant — internal
- `_cell_text` — function — internal — Convert a cell value to a normalized stripped string ("" for None).
- `_HEADING_FONT_SIZE` — constant — internal
- `_row_all_struck` — function — internal — Return True if every non-empty cell in `cells` has font.
- `XLSXExtractor` — class — pub — Extract worksheets and tables from XLSX files (FR-1).
  - `extract` — method — pub
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md) (for `DocumentIR`, `ContentBlock`, `FontInfo`, `Position`, `BlockType`).

**Depended on by**
[profiler](../profiler/MODULE.md), [parser](../parser/MODULE.md), [pipeline](../pipeline/MODULE.md) (extract stage).

**Deferred**
- DOC + XLS extractor implementations (deferred per FR-27 / D-018 — revisit: when a corpus contains these legacy formats)
