# extraction

**Purpose**
Format-aware content extraction. Each format has its own extractor (PDF via pymupdf + pdfplumber, DOCX via python-docx, XLSX via openpyxl), all producing the normalized `DocumentIR` defined in [models](../models/MODULE.md). Downstream stages (profiler, parser) treat every document uniformly after this boundary. Serves FR-1 (PDF + DOCX + XLSX extraction), FR-30 (sources read from `<env_dir>/input/<MNO>/<release>/`), and FR-33 (strikeout detection); implements D-010 (multi-format DocumentIR), D-018 (DOC/XLS deferred per FR-27), D-023 (input path layout), D-031 (strikeout detection per format).

**Public surface**
- `BaseExtractor` (base.py) — ABC: `extract(file_path, mno="", release="", doc_type="", detect_text_tables=False, header_footer_margin_mode="blanket", layout_provider="", provider_table_grid=True, images_root=None) -> DocumentIR` (per-cell PDF hints ignored by other formats; `images_root` redirects extracted-image artifacts into the build output — D-DRAFT-10 docker-distro)
- `PDFExtractor` (pdf_extractor.py) — text blocks with `FontInfo` (incl. strikethrough via PyMuPDF span flags bit 8), tables via pdfplumber, images; header/footer margin filtering. Opt-in per-cell hints (mno-c-ingestion): `layout_provider: str` routes tables + figures through a `LayoutProvider` (see below) instead of pdfplumber/`get_images` — provider tables become TABLE blocks carrying lossless `html` (+ flat grid unless `provider_table_grid=False`), figures become IMAGE blocks with `caption`, and their bboxes suppress the overlapping pymupdf paragraphs (the text layer is otherwise unchanged); `detect_text_tables: bool` enables geometric borderless-table detection (persistent column-gutter signal; region preserved as a `[TABLE]…[/TABLE]` text block, not a reconstructed grid); `header_footer_margin_mode="pattern_only"` disables blanket margin drops (keeps top-of-page requirement headings)
- `LayoutProvider` (layout_provider.py, mno-c-ingestion) — protocol mirroring `LLMProvider`/`EmbeddingProvider`: `available() -> (bool, reason)`, `extract_layout(pdf_path, image_dir, want_table_grid) -> LayoutStructures`; result types `LayoutStructures` / `LayoutTable` (page, bbox, html, headers, rows) / `LayoutFigure` (page, bbox, image_path, caption), bboxes normalized to top-left PDF points so the extractor can fuse by position; `get_layout_provider(name)` lazy registry
- `DoclingProvider` (docling_provider.py) — Docling-backed `LayoutProvider` (tables + figures only; OCR off for born-digital PDFs — pymupdf keeps the text layer). Docling is an optional heavy dep: lazy-imported, `available()` gates a missing install fail-loud. Env: `DOCLING_ARTIFACTS` (offline models dir, pair with `HF_HUB_OFFLINE=1`), `DOCLING_OCR=1` (scanned pages)
- `DOCXExtractor` (docx_extractor.py) — paragraphs (with style/level + strikethrough from `Run.font.strike`), tables (with merged-cell metadata per D-072 — populates `ContentBlock.merged_cells` with anchor `(row, col, rowspan, colspan, text)` for every `gridSpan>1` / `vMerge` region; continuation positions in `headers`/`rows` are blanked to `""` to keep the matrix rectangular), embedded images
- `XLSXExtractor` (xlsx_extractor.py) — per-sheet extraction via openpyxl: each non-empty worksheet emits a heading (sheet title) + a table block; cell strikethrough surfaced via `Cell.font.strike` for row-level drop semantics. Page numbers track sheet index (1-based).
- Registry (registry.py):
  - `supported_extensions() -> set[str]`
  - `get_extractor(file_path) -> BaseExtractor` — extension-keyed lookup; raises `ValueError` on unsupported
  - `extract_document(file_path, mno, release, doc_type, ..., images_root=None) -> DocumentIR` — passes the per-cell hints + `images_root` through to the format extractor
  - `infer_metadata_from_path(file_path, root=None) -> {mno, release, doc_type, plan}` — walks the `<root>/<MNO>/<MMMYYYY>/` convention (D-023); `root` (the configured documents root) is the reliable anchor when the path has no literal `input` segment (requirements_dir overrides / container mounts — D-DRAFT-10 docker-distro); without it, falls back to the `input`-segment then last-two-dirs heuristics (which cannot see per-plan subdirs). `doc_type` defaults to `"requirement"` (FR-26 deferred). Enforces the MMMYYYY release convention fail-loud (`EXT-E004`) via `release_key` (D-DRAFT-6, strand `multi-mno-nora`)
- `release_key.py` (D-DRAFT-6) — the `(MNO, release)` cell convention: `is_valid_release(release) -> bool` and `release_order_key(release) -> (year, month) | None` (soft checks); `release_key(release) -> (label, order_key)` (fail-loud validator, raises `ValueError`/`EXT-E004` on non-MMMYYYY); `RELEASE_RE`. Single core home for the convention (mirrors `sandbox/sira_cells.py`, which reconciles onto it at multi-mno-sira land time)
- `extract.main()` — CLI entrypoint (`python -m core.src.extraction.extract`)

**Invariants**
- Every extractor returns a valid `DocumentIR`; extractor-specific details never leak into the IR's type surface.
- Text blocks from PDF **must** carry `FontInfo` — the profiler's heading detection clusters blocks by font size/boldness and will degrade silently if this is missing.
- Block `Position.index` is a contiguous sequence starting at 0 across the whole document, reflecting reading order.
- Tables extracted by pdfplumber are de-duplicated against text blocks they overlap with (PDF text extractors surface table cells as text too) — no block should appear twice in the IR.
- Header/footer content (matched by margin thresholds + always-header regex patterns) is dropped, not emitted as paragraphs.
- Format-specific libraries (fitz, pdfplumber, python-docx, openpyxl) are imported **only** inside this module — no other `core/src/` module pulls them in.
- **Image artifacts belong to the build output, never the corpus** (D-DRAFT-10 docker-distro): with `images_root` set (the pipeline passes the cell's `out/extract/<mno>/<rel>/images`), extracted images land there with `image_path`/`images_dir` recorded relative to the cell out dir; the input corpus may be a read-only mount. Legacy next-to-source `extracted_images/` survives only for ad-hoc CLI use.
- **Layout-provider failures fail loud** (D-DRAFT-11 docker-distro): a provider named by the profile that is importable but cannot convert (missing models, broken dep chain) raises — surfacing as per-doc `EXT-E001` — rather than returning empty structures; the geometric table path is skipped when a provider is named, so silent degradation would mean tableless parses.
- Strike model is uniform across formats [D-031, D-036, D-060]: extractors **mark** strike state via per-run / per-cell flags; **no row content is dropped at extract time**. Detection is per-format; consequence is uniform.
  - **DOCX** — every paragraph / heading / table cell run carries `TextRun.struck` from `run.font.strike`. Block-level `font_info.strikethrough` = True iff every textful run in the block is struck (replaces D-031's "any-run-struck" coarse heuristic). Tables: `header_runs` and `row_runs` populated.
  - **PDF paragraph** — majority-of-characters across mixed-strike spans (50% defaults to `False`); single-run TextRun on the block reflecting block-level strike (per-character partial-strike on PDF deferred to a future ADR).
  - **PDF table (whole-table strike)** — `_table_is_struck` counts horizontal strike lines crossing ≥50% of the table width AND not aligned with a `Table.rows[*].bbox` edge (within `edge_tol=1.5pt`). Row-edge filter is critical: pdfplumber draws each row boundary as a full-width horizontal line which the unfiltered heuristic counted as a strike (D-036, addresses 93% false-positive rate observed pre-filter).
  - **PDF table (per-row cell strike)** — `_detect_struck_rows` flags rows whose interior (`y_top + 1.5 < y < y_bot - 1.5`) contains ≥1 horizontal strike line. Header row (index 0) is never marked struck — telecom tables retain their header even when all data rows are deleted. Per D-060: rows are **kept** in `block.rows`; `row_runs[i]` carries the strike flag.
  - **XLSX** — per-cell `cell.font.strike` becomes `TextRun.struck`. A row is "fully struck" when all non-empty cells are struck — used for the whole-table cascade signal but per D-060 the rows are kept.

**Key choices**
- PDF: pymupdf (fitz) for text + font metadata, pdfplumber for tables — neither alone covers both well. Pay the double-parse cost per file; cache is the IR JSON on disk.
- Font groups within a single text span are split into sub-blocks when they diverge — preserves heading detection on pages that mix body and heading fonts on one line.
- **Source line boundaries preserved** on PDF paragraph/heading blocks via `ContentBlock.lines` (`_extract_text_segments` tags each span with its pymupdf line index; `_make_group` reconstructs per-line strings). `block.text` is unchanged (`" ".join(lines) == text`) so detection regexes don't regress; consumers that need to separate a heading/title line from the body line beneath it read `lines` (a flattened single-block `text` otherwise produces a run-on sentence that blurs the section hierarchy for the synthesizer).
- Header/footer detection uses vertical margin thresholds (`HEADER_MARGIN_PT=65`, `FOOTER_MARGIN_PT=50`) plus a regex allow-list of phrases that are always header/footer regardless of position.
- Registry is an instance dict (`_EXTRACTORS`), not a class hierarchy — extractors are stateless; one instance per format.
- Path-based metadata inference (`<env_dir>/input/<MNO>/<release>/file.ext` per D-023) avoids hardcoding per-MNO dispatch; a new MNO needs no code change.
- **MMMYYYY release convention enforced at ingest** (D-DRAFT-6): `infer_metadata_from_path` validates a parsed (non-empty) release via `release_key` and raises `EXT-E004` on a non-MMMYYYY directory — the `(MNO, release)` cell key is the layout/ordering unit, so a mis-named release dir fails loud here rather than silently mis-ordering downstream. Validation is at the path→metadata boundary only; `extract_document(release=…)` (explicit-arg API) is unvalidated, so unit tests can pass arbitrary release labels. Empty release (path didn't carry the convention) is left to the caller, not raised here.
- XLSX strategy is one heading + one table per worksheet — minimal but lets the profiler still cluster headings by font size and the parser still see structured rows.
- **Layout-provider fusion** (mno-c-ingestion): for an opt-in cell, the provider *replaces* pdfplumber table detection and `get_images()` — its tables/figures are merged into the pymupdf text flow by `(page, y)` sort, and their bboxes reuse the existing `_overlaps_any_table` suppression so the underlying pymupdf cell text isn't duplicated as paragraphs. We deliberately do NOT adopt the provider's text/OCR layer: pymupdf keeps supplying paragraphs with per-span fonts (the profiler's heading detection depends on them) and born-digital text exactness/determinism is preserved (see `docs/compact/strands/mno-c-ingestion/docling-integration-design.md`).
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

`docling_provider.py`
- `DoclingProvider` — class — pub
  - `__init__` — constructor — internal
  - `_converter` — method — internal
  - `available` — method — pub
  - `extract_layout` — method — pub
- `_FIGURE_LABELS` — constant — internal
- `_TABLE_LABELS` — constant — internal
- `_caption` — function — internal
- `_page_sizes` — function — internal
- `_prov` — function — internal — Page (1-based) + bbox normalized to TOP-LEFT origin, PDF points.
- `_save_picture` — function — internal — Save a figure crop; return its absolute path (extractor relativizes it).
- `_table_grid` — function — internal — Best-effort flat (headers, rows) for the table-anchored req-id path.
- `_table_html` — function — internal
- `logger` — constant — pub

`docx_extractor.py`
- `DOCXExtractor` — class — pub — Extract paragraphs, tables, and images from DOCX files.
  - `_cell_runs` — staticmethod — internal — Flatten every paragraph's runs in a table cell into a single run list.
  - `_count_leading_page_breaks` — staticmethod — internal — Count page breaks in the paragraph before the first run that emits text.
  - `_count_trailing_page_breaks` — method — internal — Total page breaks in paragraph minus ones already counted as leading.
  - `_extract_paragraph_images` — method — internal
  - `_heading_level` — staticmethod — internal
  - `_iter_nested_tables` — method — internal — Yield every table nested inside *tbl*'s cells, recursing for
  - `_paragraph_block` — method — internal
  - `_paragraph_font` — method — internal — Synthesize a FontInfo from the first run with real font data.
  - `_paragraph_runs` — staticmethod — internal — Build a TextRun list preserving per-run strike state [D-060].
  - `_style_font_size` — staticmethod — internal — Walk the style inheritance chain for an explicit font size.
  - `_surrounding_text` — staticmethod — internal
  - `_table_block` — method — internal
  - `extract` — method — pub
- `_BODY_DEFAULT_SIZE` — constant — internal
- `_HEADING_DEFAULT_SIZE` — constant — internal
- `_HEADING_STYLE_RE` — constant — internal
- `logger` — constant — pub

`extract.py`
- `extract_file` — function — pub — Extract a single file and save the result as JSON.
- `main` — function — pub

`layout_provider.py`
- `BBox` — constant — pub
- `LayoutFigure` — dataclass — pub
- `LayoutProvider` — class — pub
  - `available` — method — pub — (is_usable, reason). False when the dependency or its models are
  - `extract_layout` — method — pub — Return tables + figures for the PDF. When `image_dir` is given, figure
- `LayoutStructures` — dataclass — pub — A provider's tables + figures for one document.
- `LayoutTable` — dataclass — pub
- `get_layout_provider` — function — pub — Resolve a provider by profile name, or None if unknown. Providers are

`pdf_extractor.py`
- `PDFExtractor` — class — pub — Extract text blocks, tables, and images from PDF files.
  - `_bboxes_overlap` — staticmethod — internal — Check if bbox A overlaps with bbox B by more than threshold of A's area.
  - `_block_to_text` — staticmethod — internal — Extract plain text from a pymupdf block dict.
  - `_collect_provider_layout` — method — internal — Parse the PDF once through the selected layout provider; group its
  - `_collect_strike_lines` — staticmethod — internal — Collect candidate strike-through line segments on a page (FR-33 [D-031]).
  - `_detect_header_footer_patterns` — method — internal — Detect text that repeats across most pages (headers/footers).
  - `_detect_struck_rows` — staticmethod — internal — Return data-row indices (0-based, header excluded) whose
  - `_emit_provider_blocks` — method — internal — Turn this page's provider tables/figures into TABLE / IMAGE blocks and
  - `_extract_impl` — method — internal
  - `_extract_text_segments` — method — internal — Extract text segments from a pymupdf text block, preserving font info.
  - `_get_surrounding_text` — staticmethod — internal — Get text from the most recent paragraph blocks on the same page.
  - `_group_by_font` — method — internal — Group contiguous segments with similar font size into blocks.
  - `_is_in_margin` — method — internal — Check if a block is in the header or footer margin.
  - `_make_group` — staticmethod — internal — Create a font group from collected segments.
  - `_matches_header_footer` — method — internal — Check if text matches a detected header/footer pattern.
  - `_overlaps_any_table` — method — internal — Check if a text block overlaps with any detected table region.
  - `_should_drop_margin` — method — internal — Whether to drop a block up front as header/footer margin.
  - `_span_struck` — staticmethod — internal — Check whether any strike line meaningfully crosses the span.
  - `_table_is_struck` — staticmethod — internal — Decide whether a table block is struck through (FR-33 [D-031]).
  - `extract` — method — pub
- `_LINE_Y_TOL` — constant — internal
- `_MAX_LINE_GAP_PT` — constant — internal
- `_MIN_GUTTER_PT` — constant — internal
- `_MIN_TABLE_ROWS` — constant — internal
- `_TABLE_TEXT_CLOSE` — constant — internal
- `_TABLE_TEXT_OPEN` — constant — internal
- `_TEXT_TABLE_SETTINGS` — constant — internal
- `_TWO_SIDED_LOOK` — constant — internal
- `_WORD_MERGE_GAP_PT` — constant — internal
- `_bbox_overlaps_any` — function — internal — True when `bbox` overlaps any of `others` by >= `min_frac` of its own
- `_clamp_bbox` — function — internal — Clamp a region bbox to page bounds so pdfplumber's crop() won't raise.
- `_find_table_regions` — function — internal — Borderless-table regions on a pdfplumber page, each ``(bbox, lines)``
- `_gutter_table_regions` — function — internal — Locate borderless-table regions by persistent column gutters.
- `_looks_tabular` — function — internal — Keep a text-detected table only when it has real tabular shape — >= 2
- `_normalize_region_text` — function — internal — Clean a region's extracted text: drop blank lines, strip trailing
- `_page_lines` — function — internal — Group a page's words into text-lines with merged x-segments.
- `_region_gutters` — function — internal — Column gutters of a line-set: x-intervals >= `min_gutter` that no word in
- `_region_text` — function — internal — Layout-preserving text of a cropped table region (falls back to plain
- `_text_table_detection_enabled` — function — internal
- `_trim_region` — function — internal — Drop TRAILING lines with no text past the first column — these are the
- `logger` — constant — pub

`registry.py`
- `_EXTRACTORS` — constant — internal
- `extract_document` — function — pub — Extract a document using the appropriate format extractor.
- `get_extractor` — function — pub — Get the appropriate extractor for a file based on its extension.
- `infer_metadata_from_path` — function — pub — Infer mno and release from folder structure (D-023, FR-30).
- `supported_extensions` — function — pub — Return the set of file extensions with registered extractors.

`release_key.py`
- `RELEASE_RE` — constant — pub
- `_MONTHS` — constant — internal
- `_MONTH_IDX` — constant — internal
- `is_valid_release` — function — pub — True iff `release` matches the MMMYYYY convention (e.g. 'Feb2026').
- `release_key` — function — pub — Validate and return ``(label, order_key)`` for a release; raise on miss.
- `release_order_key` — function — pub — Sortable key for a release label: 'Feb2026' -> (2026, 2).

`xlsx_extractor.py`
- `XLSXExtractor` — class — pub — Extract worksheets and tables from XLSX files (FR-1).
  - `extract` — method — pub
- `_BODY_FONT_SIZE` — constant — internal
- `_HEADING_FONT_SIZE` — constant — internal
- `_cell_text` — function — internal — Convert a cell value to a normalized stripped string ("" for None).
- `_cell_to_runs` — function — internal — Build a single-run TextRun list for an XLSX cell [D-060].
- `_row_all_struck` — function — internal — Return True if every non-empty cell in `cells` has font.strike=True.
- `logger` — constant — pub
<!-- END:STRUCTURE -->

**Depends on**
[models](../models/MODULE.md) (for `DocumentIR`, `ContentBlock`, `FontInfo`, `Position`, `BlockType`).

**Depended on by**
[profiler](../profiler/MODULE.md), [parser](../parser/MODULE.md), [pipeline](../pipeline/MODULE.md) (extract stage).

**Deferred**
- DOC + XLS extractor implementations (deferred per FR-27 / D-018 — revisit: when a corpus contains these legacy formats)
