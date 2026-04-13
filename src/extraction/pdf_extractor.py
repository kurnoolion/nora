"""PDF content extractor using pymupdf (text + images) and pdfplumber (tables).

Produces the normalized intermediate representation (TDD 5.1.7) from PDF files.
Font metadata on each text block is critical for the DocumentProfiler's
heading detection (font size clustering).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # pymupdf
import pdfplumber
from PIL import Image

from src.extraction.base import BaseExtractor
from src.models.document import (
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
        file_path = Path(file_path)
        logger.info(f"Extracting PDF: {file_path.name}")

        fitz_doc = fitz.open(str(file_path))
        plumber_pdf = pdfplumber.open(str(file_path))

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
                text_segments = self._extract_text_segments(fb)

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
                    block_type = BlockType.PARAGRAPH
                    metadata: dict = {}

                    all_blocks.append(
                        ContentBlock(
                            type=block_type,
                            position=Position(
                                page=page_num + 1,
                                index=0,
                                bbox=bbox,
                            ),
                            text=text,
                            font_info=font,
                            metadata=metadata,
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
        fitz_doc.close()
        plumber_pdf.close()

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

    def _extract_text_segments(
        self, block: dict
    ) -> list[dict]:
        """Extract text segments from a pymupdf text block, preserving font info."""
        segments = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                segments.append(
                    {
                        "text": text,
                        "size": round(span.get("size", 0), 1),
                        "bold": bool(span.get("flags", 0) & (1 << 4)),
                        "italic": bool(span.get("flags", 0) & (1 << 1)),
                        "font": span.get("font", ""),
                        "color": span.get("color", 0),
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
        current_texts = [segments[0]["text"]]
        current_seg = segments[0]

        for seg in segments[1:]:
            size_diff = abs(seg["size"] - current_seg["size"])
            if size_diff <= 2.0:
                current_texts.append(seg["text"])
            else:
                groups.append(self._make_group(current_texts, current_seg))
                current_texts = [seg["text"]]
                current_seg = seg

        groups.append(self._make_group(current_texts, current_seg))
        return groups

    @staticmethod
    def _make_group(texts: list[str], representative_seg: dict) -> dict:
        """Create a font group from collected texts and a representative segment."""
        all_caps = all(
            t.strip().isupper() for t in texts if t.strip() and t.strip().isalpha()
        )
        return {
            "text": " ".join(texts),
            "font_info": FontInfo(
                size=representative_seg["size"],
                bold=representative_seg["bold"],
                italic=representative_seg["italic"],
                font_name=representative_seg["font"],
                all_caps=all_caps,
                color=representative_seg["color"],
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
