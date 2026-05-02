"""PDF content extractor using pymupdf (text + images) and pdfplumber (tables).

Produces the normalized intermediate representation (TDD 5.1.7) from PDF files.
Font metadata on each text block is critical for the DocumentProfiler's
heading detection (font size clustering).
"""

from __future__ import annotations

import logging
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
)

logger = logging.getLogger(__name__)


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
            return self._extract_impl(file_path, fitz_doc, plumber_pdf, mno, release, doc_type)
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
            plumber_tables = plumber_page.find_tables()
            for table_obj in plumber_tables:
                bbox = table_obj.bbox  # (x0, y0, x1, y1) top-left origin
                # pdfplumber uses top-left origin, same as our convention
                table_bboxes.append(bbox)
                table_data = table_obj.extract()
                if not table_data or len(table_data) < 1:
                    continue
                headers = [
                    str(c).strip() if c else "" for c in table_data[0]
                ]
                rows = [
                    [str(c).strip() if c else "" for c in row]
                    for row in table_data[1:]
                ]
                # Skip degenerate tables: single column with empty or near-empty content
                non_empty_headers = [h for h in headers if h]
                total_cells = sum(1 for row in rows for c in row if c)
                if len(non_empty_headers) <= 1 and total_cells == 0:
                    continue
                # FR-33: detect when the table is struck through. PDF
                # strikethrough is geometric (horizontal lines drawn over
                # text); a table is treated as struck when multiple
                # horizontal strike lines fall within its bbox AND each
                # crosses a meaningful fraction of its width. The parser
                # then drops the block via the existing FR-33 path.
                table_struck = self._table_is_struck(bbox, strike_lines)
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

                # Skip header/footer regions
                if self._is_in_margin(bbox, page_height):
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
    def _table_is_struck(
        table_bbox: tuple[float, float, float, float],
        strike_lines: list[tuple[float, float, float]],
        min_lines: int = 2,
        min_overlap_frac: float = 0.5,
    ) -> bool:
        """Decide whether a table block is struck through (FR-33 [D-031]).

        Heuristic: count horizontal strike lines whose y-coordinate falls
        within the table's vertical extent AND that horizontally cover
        >= `min_overlap_frac` of the table's width. When at least
        `min_lines` such lines are found, the table is treated as struck.
        Avoids false positives on tables that happen to be near a single
        horizontal divider line.
        """
        x0, y0, x1, y1 = table_bbox
        table_width = x1 - x0
        if table_width <= 0 or y1 - y0 <= 0:
            return False
        crossing = 0
        for line_y, line_x0, line_x1 in strike_lines:
            if line_y < y0 or line_y > y1:
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
        for line in block.get("lines", []):
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
        return {
            "text": " ".join(texts),
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
