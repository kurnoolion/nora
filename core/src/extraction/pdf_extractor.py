"""PDF content extractor using pymupdf (text + images) and pdfplumber (tables).

Produces the normalized intermediate representation (TDD 5.1.7) from PDF files.
Font metadata on each text block is critical for the DocumentProfiler's
heading detection (font size clustering).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

# fitz (pymupdf) and pdfplumber are optional at import time so registry
# tests can run without the extraction backends installed. extract() will
# raise a clear ImportError if either is actually missing at call time.
try:
    import fitz  # pymupdf
except ImportError:  # pragma: no cover - optional dep
    fitz = None  # type: ignore[assignment]
try:
    import pdfplumber
except ImportError:  # pragma: no cover - optional dep
    pdfplumber = None  # type: ignore[assignment]

from core.src.extraction.base import BaseExtractor
from core.src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
    TextRun,
)

logger = logging.getLogger(__name__)

# Opt-in borderless-table detection (mno-c-ingestion). pdfplumber's default
# find_tables() uses the "lines" strategy — tables drawn with no ruling lines
# are invisible to it and their rows leak out as text. The "text" strategy
# infers the grid from word alignment, but run over a whole page it mis-reads
# prose as a grid (fragmenting text, dropping paragraphs). So we NEVER run it
# page-wide: we first locate a genuine table REGION by its persistent column
# gutters (a vertical whitespace strip empty across many lines with text on both
# sides — present in tables, absent in prose), then run the text strategy only
# inside that crop, where over-detection is impossible. Per-corpus opt-in via
# DocumentProfile.detect_text_tables; NORA_EXTRACT_TEXT_TABLES forces it on
# globally for debugging.
_TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
}


def _text_table_detection_enabled() -> bool:
    return os.getenv("NORA_EXTRACT_TEXT_TABLES", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _bbox_overlaps_any(
    bbox: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
    min_frac: float = 0.5,
) -> bool:
    """True when `bbox` overlaps any of `others` by >= `min_frac` of its own
    area — used to drop a text-detected table that duplicates a line-detected
    one. Boxes are (x0, y0, x1, y1)."""
    ax0, ay0, ax1, ay1 = bbox
    area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    if area <= 0:
        return False
    for bx0, by0, bx1, by1 in others:
        ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        iy = max(0.0, min(ay1, by1) - max(ay0, by0))
        if (ix * iy) / area >= min_frac:
            return True
    return False


def _looks_tabular(data: list[list] | None) -> bool:
    """Keep a text-detected table only when it has real tabular shape — >= 2
    columns AND >= 2 rows with content — so prose the text strategy mis-reads
    as a 1-column or single-row 'table' is rejected."""
    if not data or len(data) < 2:
        return False
    if max((len(r) for r in data), default=0) < 2:
        return False
    nonempty_rows = sum(1 for r in data if any((c or "").strip() for c in r))
    return nonempty_rows >= 2


# Borderless-table region detection tunables (calibrated against the MNO-C
# corpus: prose marker-gaps run ~5-6pt, real column gutters ~10-27pt).
_MIN_GUTTER_PT = 8.0      # min width of a column gutter (> prose marker-gaps)
_MIN_TABLE_ROWS = 3       # min text-lines in a region to qualify as a table
_MAX_LINE_GAP_PT = 40.0   # vertical gap that breaks a region (points, top-to-top)
_TWO_SIDED_LOOK = 4       # recent lines checked for text on both sides of a gutter
_LINE_Y_TOL = 2.0         # words within this many points of top share a text-line
_WORD_MERGE_GAP_PT = 4.0  # words closer than this join into one segment

# Markers wrapping a borderless-table region rendered as preserved text. A grid
# reconstruction of a borderless table with wrapped/multi-line cells and ragged
# columns is unreliable, so the region is kept as clean, layout-preserved text
# (readable for retrieval/synthesis) demarcated by these markers rather than a
# mangled column/row grid.
_TABLE_TEXT_OPEN = "[TABLE]"
_TABLE_TEXT_CLOSE = "[/TABLE]"


def _page_lines(
    words: list[dict],
    *,
    y_tol: float = _LINE_Y_TOL,
    merge_gap: float = _WORD_MERGE_GAP_PT,
) -> list[dict]:
    """Group a page's words into text-lines with merged x-segments.

    Words within `y_tol` of each other's top share a line; within a line, words
    closer than `merge_gap` join into one segment (so a wider gap survives as a
    potential column gutter). Returns lines sorted by top, each
    ``{'top','bottom','segs':[(x0,x1)]}``. Pure over word dicts so the geometry
    is unit-testable without a real PDF.
    """
    buckets: dict[int, list[dict]] = {}
    for w in words:
        buckets.setdefault(round(float(w["top"]) / y_tol), []).append(w)
    lines: list[dict] = []
    for key in sorted(buckets):
        ws = sorted(buckets[key], key=lambda w: float(w["x0"]))
        top = min(float(w["top"]) for w in ws)
        bottom = max(float(w["bottom"]) for w in ws)
        segs: list[tuple[float, float]] = []
        a, b = float(ws[0]["x0"]), float(ws[0]["x1"])
        for w in ws[1:]:
            x0, x1 = float(w["x0"]), float(w["x1"])
            if x0 - b > merge_gap:
                segs.append((a, b))
                a, b = x0, x1
            else:
                b = max(b, x1)
        segs.append((a, b))
        lines.append({"top": top, "bottom": bottom, "segs": segs})
    lines.sort(key=lambda ln: ln["top"])
    return lines


def _region_gutters(
    region: list[dict],
    min_gutter: float,
) -> tuple[list[tuple[float, float]], float, float]:
    """Column gutters of a line-set: x-intervals >= `min_gutter` that no word in
    any line covers (gaps in the union of every line's segments). Returns
    ``(gutters, xmin, xmax)``. A gutter is empty-in-all-lines and interior by
    construction (it lies between covered spans)."""
    segs = sorted(s for ln in region for s in ln["segs"])
    if not segs:
        return [], 0.0, 0.0
    merged: list[list[float]] = [list(segs[0])]
    xmin, xmax = segs[0][0], segs[0][1]
    for a, b in segs[1:]:
        xmax = max(xmax, b)
        if a <= merged[-1][1] + 0.01:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gutters = [
        (merged[k][1], merged[k + 1][0])
        for k in range(len(merged) - 1)
        if merged[k + 1][0] - merged[k][1] >= min_gutter
    ]
    return gutters, xmin, xmax


def _normalize_region_text(txt: str | None) -> str:
    """Clean a region's extracted text: drop blank lines, strip trailing
    whitespace, and remove the common leading indent that layout-mode extraction
    pads on (crop coordinates start at page x=0). Preserves inter-column spacing
    on each line so the table stays readable. Returns "" for empty input."""
    lines = [ln.rstrip() for ln in (txt or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    indent = min(len(ln) - len(ln.lstrip()) for ln in lines)
    return "\n".join(ln[indent:] for ln in lines)


def _region_text(crop) -> str:
    """Layout-preserving text of a cropped table region (falls back to plain
    text, then ""), normalized. Best-effort — never raises."""
    txt = None
    try:
        txt = crop.extract_text(layout=True)
    except Exception:  # noqa: BLE001 — layout mode can fail on odd regions
        txt = None
    if not txt:
        try:
            txt = crop.extract_text()
        except Exception:  # noqa: BLE001
            txt = None
    return _normalize_region_text(txt)


def _trim_region(region: list[dict], min_gutter: float, min_rows: int) -> list[dict]:
    """Drop TRAILING lines with no text past the first column — these are the
    single-column prose the two-sided look-back window let the region bleed into
    below a far-right column. Only trailing lines are trimmed: leading lines are
    kept because a multi-line header cell can occupy the top of the region as a
    single-column line, and trimming it would split the header off the table.
    Interior single-column lines (wrapped-cell continuations) are always kept.
    Never trims below `min_rows`."""
    gutters, _, _ = _region_gutters(region, min_gutter)
    if not gutters:
        return region
    right_edge = gutters[0][1]  # right edge of the leftmost gutter

    def _has_right(ln: dict) -> bool:
        return any(s[0] >= right_edge - 0.5 for s in ln["segs"])

    hi = len(region)
    while hi > min_rows and not _has_right(region[hi - 1]):
        hi -= 1
    return region[:hi]


def _gutter_table_regions(
    lines: list[dict],
    *,
    min_gutter: float = _MIN_GUTTER_PT,
    min_rows: int = _MIN_TABLE_ROWS,
    max_line_gap: float = _MAX_LINE_GAP_PT,
    look: int = _TWO_SIDED_LOOK,
) -> list[tuple[tuple[float, float, float, float], list[dict]]]:
    """Locate borderless-table regions by persistent column gutters.

    A table (unlike prose) has a vertical whitespace gutter that stays empty
    across many consecutive lines with text on BOTH sides. Grow a region line by
    line while (a) the vertical gap stays within `max_line_gap`, (b) >=1 gutter
    of width >= `min_gutter` remains empty across all region lines, and (c) among
    the most recent `look` lines some has text left of that gutter and some right
    — the two-sided check stops the region bleeding into single-column prose
    below a far-right column. Regions with >= `min_rows` lines yield an
    ``(bbox, member_lines)`` pair — the member lines let the caller derive column
    boundaries (`_column_separators`) for the crop. Pure over `_page_lines`
    output; validated against real MNO-C prose/table pages.
    """
    n = len(lines)
    regions: list[tuple[tuple[float, float, float, float], list[dict]]] = []
    i = 0
    while i < n:
        region = [lines[i]]
        j = i
        while j + 1 < n:
            if lines[j + 1]["top"] - lines[j]["top"] > max_line_gap:
                break
            trial = region + [lines[j + 1]]
            gutters, _, _ = _region_gutters(trial, min_gutter)
            if not gutters:
                break
            recent = trial[-look:]
            two_sided = False
            for ga, gb in gutters:
                left = any(any(s[1] <= ga + 0.5 for s in ln["segs"]) for ln in recent)
                right = any(any(s[0] >= gb - 0.5 for s in ln["segs"]) for ln in recent)
                if left and right:
                    two_sided = True
                    break
            if not two_sided:
                break
            region = trial
            j += 1
        region = _trim_region(region, min_gutter, min_rows)
        gutters, xmin, xmax = _region_gutters(region, min_gutter)
        if len(region) >= min_rows and gutters:
            top = region[0]["top"]
            bottom = region[-1]["bottom"]
            bbox = (xmin - 2.0, top - 2.0, xmax + 2.0, bottom + 2.0)
            regions.append((bbox, region))
        i = j + 1 if j > i else i + 1
    return regions


def _clamp_bbox(
    bbox: tuple[float, float, float, float],
    width: float,
    height: float,
) -> tuple[float, float, float, float]:
    """Clamp a region bbox to page bounds so pdfplumber's crop() won't raise."""
    x0, top, x1, bottom = bbox
    return (
        max(0.0, x0),
        max(0.0, top),
        min(width, x1),
        min(height, bottom),
    )


def _find_table_regions(
    plumber_page,
) -> list[tuple[tuple[float, float, float, float], list[dict]]]:
    """Borderless-table regions on a pdfplumber page, each ``(bbox, lines)``
    (best-effort; returns [] on any extract_words failure so a bad page never
    aborts extraction)."""
    try:
        words = plumber_page.extract_words(use_text_flow=False)
    except Exception:  # noqa: BLE001 — detection must never abort the page
        return []
    return _gutter_table_regions(_page_lines(words))


class PDFExtractor(BaseExtractor):
    """Extract text blocks, tables, and images from PDF files."""

    # Margin thresholds (points) for header/footer detection
    HEADER_MARGIN_PT = 65
    FOOTER_MARGIN_PT = 50

    # Minimum area (pt^2) for a text block to be considered content
    MIN_BLOCK_AREA = 10

    # Patterns that are always header/footer regardless of position
    PAGE_NUMBER_RE = re.compile(r"^\s*Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE)
    CONFIDENTIAL_RE = re.compile(
        r"(Official Use Only|Proprietary.*Confidential|Non-Disclosure)", re.IGNORECASE
    )

    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
        detect_text_tables: bool = False,
        header_footer_margin_mode: str = "blanket",
    ) -> DocumentIR:
        if fitz is None or pdfplumber is None:
            raise ImportError(
                "PDFExtractor requires pymupdf and pdfplumber. "
                "Install with: pip install pymupdf pdfplumber"
            )
        file_path = Path(file_path)
        logger.info(f"Extracting PDF: {file_path.name}")

        fitz_doc = fitz.open(str(file_path))
        try:
            plumber_pdf = pdfplumber.open(str(file_path))
        except Exception:
            fitz_doc.close()
            raise
        try:
            return self._extract_impl(
                file_path, fitz_doc, plumber_pdf, mno, release, doc_type,
                detect_text_tables=detect_text_tables,
                header_footer_margin_mode=header_footer_margin_mode,
            )
        finally:
            fitz_doc.close()
            plumber_pdf.close()

    def _extract_impl(
        self,
        file_path: Path,
        fitz_doc: fitz.Document,
        plumber_pdf: pdfplumber.PDF,
        mno: str,
        release: str,
        doc_type: str,
        detect_text_tables: bool = False,
        header_footer_margin_mode: str = "blanket",
    ) -> DocumentIR:
        # First pass: detect repeating header/footer text across pages
        header_footer_patterns = self._detect_header_footer_patterns(fitz_doc)
        logger.info(
            f"Detected {len(header_footer_patterns)} header/footer patterns"
        )

        all_blocks: list[ContentBlock] = []
        images_dir = (
            file_path.parent / "extracted_images" / file_path.stem
        )

        for page_num in range(len(fitz_doc)):
            page = fitz_doc[page_num]
            plumber_page = plumber_pdf.pages[page_num]
            page_height = page.rect.height

            # --- Strike-through line candidates (FR-33 [D-031]) ---
            # Collected once per page; used both by table strike detection
            # (immediately below) and by the per-span strike check
            # inside `_extract_text_segments` for paragraph blocks.
            strike_lines = self._collect_strike_lines(page)

            # --- Tables (pdfplumber) ---
            table_bboxes: list[tuple[float, float, float, float]] = []
            plumber_tables = list(plumber_page.find_tables())
            # Opt-in: borderless tables. Locate each table REGION by its
            # persistent column gutters (never page-wide — that mis-reads prose),
            # then keep it as layout-preserved TEXT wrapped in [TABLE] markers
            # rather than a reconstructed grid: borderless tables with wrapped
            # multi-line cells and ragged columns don't reconstruct into a clean
            # grid, and a mangled grid reads worse than the plain text. The block
            # is emitted at the region's position and its bbox is added to
            # table_bboxes, so the individual rows stop leaking out as paragraphs
            # the parser mis-reads as sections / merges into the requirement prose.
            if detect_text_tables:
                line_bboxes = [t.bbox for t in plumber_tables]
                page_w, page_h = plumber_page.width, plumber_page.height
                for region_bbox, _region_lines in _find_table_regions(plumber_page):
                    if _bbox_overlaps_any(region_bbox, line_bboxes):
                        continue
                    try:
                        crop = plumber_page.crop(
                            _clamp_bbox(region_bbox, page_w, page_h)
                        )
                        region_text = _region_text(crop)
                    except Exception as exc:  # noqa: BLE001 — a bad region shouldn't abort the page
                        logger.debug("borderless table skipped: %s", exc)
                        continue
                    if not region_text:
                        continue
                    table_bboxes.append(region_bbox)  # suppress the rows below
                    demarcated = (
                        f"{_TABLE_TEXT_OPEN}\n{region_text}\n{_TABLE_TEXT_CLOSE}"
                    )
                    all_blocks.append(
                        ContentBlock(
                            type=BlockType.PARAGRAPH,
                            position=Position(
                                page=page_num + 1, index=0, bbox=region_bbox
                            ),
                            text=demarcated,
                            font_info=FontInfo(size=10.0),
                            runs=[TextRun(text=demarcated, struck=False)],
                        )
                    )
            for table_obj in plumber_tables:
                bbox = table_obj.bbox  # (x0, y0, x1, y1) top-left origin
                # NOTE: bbox is reserved in `table_bboxes` (which suppresses the
                # paragraph text beneath it) ONLY once we commit to keeping the
                # table — see just before the ContentBlock append below. A table
                # we skip here (empty / 1×1 hallucination) must NOT suppress its
                # underlying text, or the content vanishes from the IR entirely.
                table_data = table_obj.extract()
                if not table_data or len(table_data) < 1:
                    logger.debug("table skipped (no data) at %s", bbox)
                    continue
                headers = [
                    str(c).strip() if c else "" for c in table_data[0]
                ]
                rows = [
                    [str(c).strip() if c else "" for c in row]
                    for row in table_data[1:]
                ]
                # Drop only when every cell across headers + body is
                # empty. The 1×1-hallucination filter below catches the
                # pdfplumber-fabricated single-cell tables; this filter
                # is just a "no content anywhere" guard. (Loosened
                # alongside the docx extractor — both shared the same
                # accidentally-too-tight shape.)
                non_empty_headers = sum(1 for h in headers if h)
                non_empty_body = sum(1 for row in rows for c in row if c)
                if non_empty_headers == 0 and non_empty_body == 0:
                    logger.debug("table skipped (all cells empty) at %s", bbox)
                    continue
                # Skip 1×1 "tables" — pdfplumber commonly hallucinates a
                # 1-row × 1-column "table" around small column-aligned
                # text regions (e.g. the small-font req_id markers in
                # VZW OA). These are paragraph fragments already
                # extracted by PyMuPDF, not real tables. Real 1×1 tables
                # are essentially single cells and almost never occur in
                # technical specs.
                if (
                    len(rows) == 1
                    and len(rows[0]) == 1
                    and non_empty_headers <= 1
                ):
                    logger.debug("table skipped (1×1 hallucination) at %s", bbox)
                    continue
                # FR-33: detect strike-through at row AND table level.
                # Row-level (per-row data-cell strikes) drops just the
                # struck rows; table-level (whole-table whole-strike)
                # marks the whole block strikethrough so the parser
                # drops it. Detected separately:
                #   - Row-level: strike line in row INTERIOR (mid-row,
                #     where cell text is drawn) → drop the row
                #   - Table-level: 2+ full-width strike lines crossing
                #     the table, NOT aligned with row-grid edges
                row_edge_ys: list[float] = []
                for row_obj in table_obj.rows:
                    try:
                        rb = row_obj.bbox
                        row_edge_ys.append(rb[1])
                        row_edge_ys.append(rb[3])
                    except (AttributeError, IndexError, TypeError):
                        continue
                struck_row_indices = self._detect_struck_rows(
                    table_obj, strike_lines
                )
                # D-060: do NOT filter struck rows out of `rows`. Keep
                # them, mark per-row strike via row_runs so downstream
                # consumers (parser, UI) can decide what to do. The
                # parser still drops struck rows by default
                # (profile.ignore_strikeout); the data stays in the IR
                # for audit and partial-strike rendering.
                strike_min = 1 if len(rows) <= 1 else 2
                table_struck = self._table_is_struck(
                    bbox,
                    strike_lines,
                    min_lines=strike_min,
                    row_edge_ys=row_edge_ys,
                )
                # If every body row is struck (per-row detection caught
                # them all) and no header is detected, treat as
                # whole-table strike — gives the parser the same cascade
                # behavior the previous "drop emptied-table" path had.
                if (
                    rows
                    and len(struck_row_indices) == len(rows)
                    and not table_struck
                ):
                    table_struck = True
                # Build header_runs / row_runs (single-run cells; PDF is
                # character-level, no per-run preservation).
                header_runs = [[TextRun(text=h, struck=False)] for h in headers]
                row_runs = [
                    [
                        [TextRun(text=cell, struck=(i in struck_row_indices))]
                        for cell in row
                    ]
                    for i, row in enumerate(rows)
                ]
                # Table committed — now reserve its bbox so the paragraph pass
                # suppresses the text underneath it (that text IS this table).
                table_bboxes.append(bbox)
                all_blocks.append(
                    ContentBlock(
                        type=BlockType.TABLE,
                        position=Position(
                            page=page_num + 1,
                            index=0,  # assigned later
                            bbox=bbox,
                        ),
                        headers=headers,
                        rows=rows,
                        header_runs=header_runs,
                        row_runs=row_runs,
                        font_info=FontInfo(
                            size=12.0,
                            strikethrough=table_struck,
                        ),
                    )
                )

            # --- Text blocks (pymupdf) ---
            fitz_blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)[
                "blocks"
            ]
            for fb in fitz_blocks:
                if fb["type"] != 0:  # skip image blocks (handled below)
                    continue

                bbox = (fb["bbox"][0], fb["bbox"][1], fb["bbox"][2], fb["bbox"][3])

                # Skip tiny blocks
                width = bbox[2] - bbox[0]
                height = bbox[3] - bbox[1]
                if width * height < self.MIN_BLOCK_AREA:
                    continue

                # Skip header/footer regions. In "blanket" mode any margin block
                # is dropped up front; in "pattern_only" mode margin text is kept
                # and removed below only if it matches a header/footer /
                # page-number / confidential pattern — so a requirement heading
                # that begins at the very top of a page isn't silently discarded.
                if self._should_drop_margin(
                    bbox, page_height, header_footer_margin_mode
                ):
                    continue

                # Skip blocks that overlap with detected tables
                if self._overlaps_any_table(bbox, table_bboxes):
                    continue

                # Process spans into content blocks
                text_segments = self._extract_text_segments(fb, strike_lines)

                # Skip if this matches a header/footer pattern
                full_text = " ".join(seg["text"] for seg in text_segments)
                if self._matches_header_footer(full_text, header_footer_patterns):
                    continue
                if self.PAGE_NUMBER_RE.match(full_text):
                    continue
                if self.CONFIDENTIAL_RE.search(full_text):
                    continue

                if not text_segments:
                    continue

                # Group segments by font characteristics to split mixed-font blocks
                groups = self._group_by_font(text_segments)
                for group in groups:
                    text = group["text"].strip()
                    if not text:
                        continue
                    font = group["font_info"]
                    # D-060: PDF paragraph runs are coarse — one TextRun
                    # per block, struck=block-level strike. Per-character
                    # partial-strike on PDF would require per-span line
                    # geometry testing (future ADR).
                    runs = [TextRun(text=text, struck=bool(font.strikethrough))]

                    all_blocks.append(
                        ContentBlock(
                            type=BlockType.PARAGRAPH,
                            position=Position(
                                page=page_num + 1,
                                index=0,
                                bbox=bbox,
                            ),
                            text=text,
                            font_info=font,
                            runs=runs,
                            lines=group.get("lines", []),
                        )
                    )

            # --- Images (pymupdf) ---
            for img_idx, img_info in enumerate(page.get_images()):
                xref = img_info[0]
                try:
                    base_image = fitz_doc.extract_image(xref)
                    if not base_image or base_image["width"] < 20 or base_image["height"] < 20:
                        continue  # skip tiny images (likely decorative)

                    img_ext = base_image["ext"]
                    img_filename = f"p{page_num + 1}_{img_idx:03d}.{img_ext}"
                    img_path = images_dir / img_filename

                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(img_path, "wb") as f:
                        f.write(base_image["image"])

                    # Get surrounding text for context
                    surrounding = self._get_surrounding_text(
                        all_blocks, page_num + 1
                    )

                    all_blocks.append(
                        ContentBlock(
                            type=BlockType.IMAGE,
                            position=Position(
                                page=page_num + 1,
                                index=0,
                                bbox=None,
                            ),
                            image_path=str(img_path.relative_to(file_path.parent)),
                            surrounding_text=surrounding,
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to extract image xref={xref} on page {page_num + 1}: {e}"
                    )

        total_pages = len(fitz_doc)

        # Sort by page, then vertical position (y0 of bbox)
        all_blocks.sort(
            key=lambda b: (
                b.position.page,
                b.position.bbox[1] if b.position.bbox else 9999,
            )
        )

        # Assign sequential indices
        for i, block in enumerate(all_blocks):
            block.position.index = i

        doc_ir = DocumentIR(
            source_file=file_path.name,
            source_format="pdf",
            mno=mno,
            release=release,
            doc_type=doc_type,
            content_blocks=all_blocks,
            extraction_metadata={
                "page_count": total_pages,
                "header_footer_patterns": header_footer_patterns,
                "images_dir": str(images_dir.relative_to(file_path.parent))
                if images_dir.exists()
                else None,
            },
        )

        logger.info(
            f"Extracted {file_path.name}: {doc_ir.block_count} blocks "
            f"({len(doc_ir.blocks_by_type(BlockType.PARAGRAPH))} text, "
            f"{len(doc_ir.blocks_by_type(BlockType.TABLE))} tables, "
            f"{len(doc_ir.blocks_by_type(BlockType.IMAGE))} images)"
        )

        return doc_ir

    # --- Header/footer detection ---

    def _detect_header_footer_patterns(
        self, doc: fitz.Document, sample_pages: int = 20
    ) -> list[str]:
        """Detect text that repeats across most pages (headers/footers).

        Samples the first N pages, finds text blocks that appear on >60% of them.
        """
        pages_to_sample = min(sample_pages, len(doc))
        margin_texts: dict[str, int] = {}

        for page_num in range(pages_to_sample):
            page = doc[page_num]
            page_height = page.rect.height
            blocks = page.get_text("dict")["blocks"]
            seen_on_page: set[str] = set()

            for b in blocks:
                if b["type"] != 0:
                    continue
                bbox = b["bbox"]
                # Only look at blocks near top or bottom margins
                if bbox[1] < self.HEADER_MARGIN_PT or bbox[3] > page_height - self.FOOTER_MARGIN_PT:
                    text = self._block_to_text(b).strip()
                    # Normalize page numbers: replace digits with placeholder
                    normalized = re.sub(r"\d+", "#", text)
                    if normalized and normalized not in seen_on_page:
                        seen_on_page.add(normalized)
                        margin_texts[normalized] = margin_texts.get(normalized, 0) + 1

        threshold = pages_to_sample * 0.6
        patterns = [
            text for text, count in margin_texts.items() if count >= threshold
        ]
        return patterns

    def _should_drop_margin(
        self,
        bbox: tuple[float, float, float, float],
        page_height: float,
        margin_mode: str,
    ) -> bool:
        """Whether to drop a block up front as header/footer margin.

        "blanket" (default) drops any block in the margin band. "pattern_only"
        never drops here — the block falls through to the pattern / page-number /
        confidential checks, so genuine content (e.g. a requirement heading at the
        top of a page) survives while true repeating headers are still removed.
        """
        if margin_mode == "pattern_only":
            return False
        return self._is_in_margin(bbox, page_height)

    def _is_in_margin(
        self,
        bbox: tuple[float, float, float, float],
        page_height: float,
    ) -> bool:
        """Check if a block is in the header or footer margin."""
        y_top = bbox[1]
        y_bottom = bbox[3]
        if y_top < self.HEADER_MARGIN_PT and y_bottom < self.HEADER_MARGIN_PT:
            return True
        if y_top > page_height - self.FOOTER_MARGIN_PT:
            return True
        return False

    def _matches_header_footer(
        self, text: str, patterns: list[str]
    ) -> bool:
        """Check if text matches a detected header/footer pattern."""
        normalized = re.sub(r"\d+", "#", text.strip())
        return normalized in patterns

    # --- Table overlap detection ---

    def _overlaps_any_table(
        self,
        text_bbox: tuple[float, float, float, float],
        table_bboxes: list[tuple[float, float, float, float]],
    ) -> bool:
        """Check if a text block overlaps with any detected table region."""
        for tbbox in table_bboxes:
            if self._bboxes_overlap(text_bbox, tbbox):
                return True
        return False

    @staticmethod
    def _bboxes_overlap(
        a: tuple[float, float, float, float],
        b: tuple[float, float, float, float],
        threshold: float = 0.5,
    ) -> bool:
        """Check if bbox A overlaps with bbox B by more than threshold of A's area."""
        x_overlap = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        y_overlap = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        overlap_area = x_overlap * y_overlap
        a_area = (a[2] - a[0]) * (a[3] - a[1])
        if a_area <= 0:
            return False
        return (overlap_area / a_area) > threshold

    # --- Text block processing ---

    @staticmethod
    def _collect_strike_lines(page) -> list[tuple[float, float, float]]:
        """Collect candidate strike-through line segments on a page (FR-33 [D-031]).

        PyMuPDF span flags do not include strikethrough; PDF strike marks are
        graphic operations (a horizontal line drawn over text). We harvest
        nearly-horizontal short stroke segments from `page.get_drawings()`
        and use them later as candidates that may cross over text spans.

        Heuristic: a line counts as a strike candidate when its vertical
        run is ≤ 1.5pt (true horizontals only) and its horizontal run is
        ≥ 5pt (rules out tiny artifacts). Vertical lines (table borders)
        and rectangles are filtered. Returns [(y_center, x0, x1), ...].
        """
        lines: list[tuple[float, float, float]] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            return lines
        for d in drawings:
            for item in d.get("items", []):
                if not item or item[0] != "l":  # 'l' = line; rectangles, curves skipped
                    continue
                try:
                    p1, p2 = item[1], item[2]
                    dy = abs(p1.y - p2.y)
                    dx = abs(p2.x - p1.x)
                except (AttributeError, IndexError):
                    continue
                if dy <= 1.5 and dx >= 5.0:
                    y_c = (p1.y + p2.y) / 2
                    x0, x1 = sorted([p1.x, p2.x])
                    lines.append((y_c, x0, x1))
        return lines

    @staticmethod
    def _detect_struck_rows(
        table_obj,
        strike_lines: list[tuple[float, float, float]],
        edge_tol: float = 1.5,
    ) -> list[int]:
        """Return data-row indices (0-based, header excluded) whose
        interior contains strike-through line segments (FR-33 [D-031]).

        A row is treated as struck when ≥1 candidate strike line falls
        in its vertical interior — strictly between `row.bbox[1]+tol`
        and `row.bbox[3]-tol`. The edge-tolerance window keeps row-grid
        lines (top/bottom of each row) out. Strike-throughs of cell
        text draw at the middle of a text line, well inside the cell's
        vertical extent, so they remain even after the edge filter.

        The `strike_lines` from `_collect_strike_lines` are already
        length-filtered (≥5pt), so a single in-row line is enough
        signal — real-world strikes typically draw multiple short
        segments per word, but we don't require that to avoid missing
        single-word cell strikes.

        First row of `table_obj.rows` is treated as the header (matches
        the extract-loop convention `headers = table_data[0]`,
        `rows = table_data[1:]`) and is never marked struck — VZW OA
        tables retain their header row even when all data rows are
        deleted.
        """
        struck: list[int] = []
        rows_list = list(table_obj.rows)
        if len(rows_list) < 2:
            return struck  # header-only or empty
        for i, row in enumerate(rows_list[1:]):
            try:
                rb = row.bbox
            except (AttributeError, IndexError, TypeError):
                continue
            y_top, y_bot = rb[1] + edge_tol, rb[3] - edge_tol
            if y_top >= y_bot:
                continue  # row too thin
            for line_y, _, _ in strike_lines:
                if y_top < line_y < y_bot:
                    struck.append(i)
                    break
        return struck

    @staticmethod
    def _table_is_struck(
        table_bbox: tuple[float, float, float, float],
        strike_lines: list[tuple[float, float, float]],
        min_lines: int = 2,
        min_overlap_frac: float = 0.5,
        row_edge_ys: list[float] | None = None,
        edge_tol: float = 1.5,
    ) -> bool:
        """Decide whether a table block is struck through (FR-33 [D-031]).

        Heuristic: count horizontal strike lines whose y-coordinate falls
        within the table's vertical extent AND that horizontally cover
        >= `min_overlap_frac` of the table's width AND do NOT coincide
        with any of `row_edge_ys` (within `edge_tol` points). When at
        least `min_lines` such lines are found, the table is treated as
        struck.

        The row-edge filter is critical: pdfplumber draws each row
        boundary as a horizontal line of the full cell width, and PyMuPDF
        surfaces those as candidates indistinguishable from strike-
        throughs by geometry alone. Without filtering, an N-row table
        with grid lines trivially exceeds `min_lines=2` (every divider
        looks like a strike), producing false positives at ~93% of
        real-world tables. Real strike-throughs draw at the middle of a
        text row, well away from the row-boundary y; the `edge_tol=1.5`
        window catches paired top-of-row-i / bottom-of-row-(i-1) draws
        that PDF generators sometimes emit twice at adjacent ys.
        """
        x0, y0, x1, y1 = table_bbox
        table_width = x1 - x0
        if table_width <= 0 or y1 - y0 <= 0:
            return False
        edges = row_edge_ys or []
        crossing = 0
        for line_y, line_x0, line_x1 in strike_lines:
            if line_y < y0 or line_y > y1:
                continue
            # Skip lines that align with a row boundary — these are grid
            # lines, not strike marks. Tolerance handles paired-edge
            # draws (e.g. y=615.73 and y=616.48 both belonging to the
            # same nominal row edge at 616.1).
            if edges and any(abs(line_y - e) <= edge_tol for e in edges):
                continue
            overlap = min(x1, line_x1) - max(x0, line_x0)
            if overlap >= table_width * min_overlap_frac:
                crossing += 1
                if crossing >= min_lines:
                    return True
        return False

    @staticmethod
    def _span_struck(
        span_bbox: tuple[float, float, float, float],
        strike_lines: list[tuple[float, float, float]],
        min_overlap_frac: float = 0.5,
    ) -> bool:
        """Check whether any strike line meaningfully crosses the span.

        A line counts as struck-through when:
          - it sits within ±40% of the span's height of the span midline
            (so we accept marks slightly above center, where strike-through
            usually falls), and
          - it horizontally covers ≥ `min_overlap_frac` of the span width
            (rules out tick marks, dividers).
        """
        x0, y0, x1, y1 = span_bbox
        span_w = x1 - x0
        if span_w <= 0:
            return False
        span_mid_y = (y0 + y1) / 2
        tol = max(2.0, (y1 - y0) * 0.4)
        for line_y, line_x0, line_x1 in strike_lines:
            if abs(line_y - span_mid_y) > tol:
                continue
            overlap = min(x1, line_x1) - max(x0, line_x0)
            if overlap >= span_w * min_overlap_frac:
                return True
        return False

    def _extract_text_segments(
        self,
        block: dict,
        strike_lines: list[tuple[float, float, float]] | None = None,
    ) -> list[dict]:
        """Extract text segments from a pymupdf text block, preserving font info.

        When `strike_lines` is supplied, each segment is tagged with a
        per-span strikethrough flag (FR-33 [D-031]); the block-level
        majority-of-characters aggregation happens in `_make_group`.
        """
        segments = []
        strike_lines = strike_lines or []
        for line_idx, line in enumerate(block.get("lines", [])):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                struck = (
                    self._span_struck(bbox, strike_lines) if strike_lines else False
                )
                segments.append(
                    {
                        "text": text,
                        "size": round(span.get("size", 0), 1),
                        "bold": bool(span.get("flags", 0) & (1 << 4)),
                        "italic": bool(span.get("flags", 0) & (1 << 1)),
                        "font": span.get("font", ""),
                        "color": span.get("color", 0),
                        "strikethrough": struck,
                        "len": len(text.strip()),
                        # Source line index within the pymupdf block — used to
                        # reconstruct line boundaries in _make_group so a
                        # heading/title line stays distinct from body text.
                        "line": line_idx,
                    }
                )
        return segments

    def _group_by_font(
        self, segments: list[dict]
    ) -> list[dict]:
        """Group contiguous segments with similar font size into blocks.

        Splits when font size differs by more than 2pt — this separates
        heading text (14pt) from inline requirement IDs (7pt), for example.
        """
        if not segments:
            return []

        groups = []
        current_segs: list[dict] = [segments[0]]

        for seg in segments[1:]:
            size_diff = abs(seg["size"] - current_segs[-1]["size"])
            if size_diff <= 2.0:
                current_segs.append(seg)
            else:
                groups.append(self._make_group(current_segs))
                current_segs = [seg]

        groups.append(self._make_group(current_segs))
        return groups

    @staticmethod
    def _make_group(segs: list[dict]) -> dict:
        """Create a font group from collected segments.

        Block-level strikethrough is the majority-of-characters across the
        constituent spans (FR-33 [D-031]): struck_chars > 50% flips the flag.
        Exactly 50% defaults to False (no drop on ambiguity).
        """
        texts = [s["text"] for s in segs]
        all_caps = all(
            t.strip().isupper() for t in texts if t.strip() and t.strip().isalpha()
        )
        struck_chars = sum(s.get("len", 0) for s in segs if s.get("strikethrough"))
        total_chars = sum(s.get("len", 0) for s in segs)
        strikethrough = struck_chars * 2 > total_chars  # strictly >50%
        rep = segs[0]
        # Reconstruct source line boundaries: join spans within a line by
        # space, emit one entry per source line. `" ".join(lines) == text`,
        # so block.text is byte-identical to before (no detection regression);
        # `lines` just preserves where the heading/title line ends and the body
        # begins. Segments without a "line" key (legacy callers) collapse to a
        # single line, matching the old behavior.
        lines: list[str] = []
        cur_line = None
        parts: list[str] = []
        for s in segs:
            li = s.get("line")
            # The `and parts` guard means the first segment never spuriously
            # flushes, so a plain None start is safe even when `li` is None.
            if li != cur_line and parts:
                lines.append(" ".join(parts))
                parts = []
            cur_line = li
            parts.append(s["text"])
        if parts:
            lines.append(" ".join(parts))
        lines = [ln.strip() for ln in lines if ln.strip()]
        return {
            "text": " ".join(texts),
            "lines": lines,
            "font_info": FontInfo(
                size=rep["size"],
                bold=rep["bold"],
                italic=rep["italic"],
                font_name=rep["font"],
                all_caps=all_caps,
                color=rep["color"],
                strikethrough=strikethrough,
            ),
        }

    @staticmethod
    def _block_to_text(block: dict) -> str:
        """Extract plain text from a pymupdf block dict."""
        parts = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                parts.append(span.get("text", ""))
        return " ".join(parts)

    @staticmethod
    def _get_surrounding_text(
        blocks: list[ContentBlock], page: int, max_chars: int = 200
    ) -> str:
        """Get text from the most recent paragraph blocks on the same page."""
        page_texts = []
        for b in reversed(blocks):
            if b.position.page != page:
                continue
            if b.type == BlockType.PARAGRAPH and b.text:
                page_texts.append(b.text[:max_chars])
                if len(page_texts) >= 2:
                    break
        return " ".join(reversed(page_texts))
