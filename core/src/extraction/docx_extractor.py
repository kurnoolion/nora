"""DOCX content extractor using python-docx.

Produces the normalized intermediate representation (TDD 5.1.7) from DOCX files.
DOCX exposes explicit heading styles (Heading 1..9) and run-level font
properties. We map style-based headings into block.level/style and
synthesize a FontInfo so the DocumentProfiler's font-size clustering
works the same way it does for PDFs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

from core.src.extraction.base import BaseExtractor
from core.src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
)

logger = logging.getLogger(__name__)


# Default font sizes (pt) for paragraphs that don't carry explicit run
# font size. Tuned so _detect_headings can still cluster by size.
_HEADING_DEFAULT_SIZE = {
    1: 18.0,
    2: 16.0,
    3: 14.0,
    4: 13.0,
    5: 12.0,
    6: 11.5,
    7: 11.0,
    8: 11.0,
    9: 11.0,
}
_BODY_DEFAULT_SIZE = 11.0

_HEADING_STYLE_RE = re.compile(r"^heading\s*(\d+)$", re.IGNORECASE)

# Req-ID marker used to identify the title/id split point in a heading.
_VZ_REQ_MARKER = "VZ_REQ_"


class DOCXExtractor(BaseExtractor):
    """Extract paragraphs, tables, and images from DOCX files."""

    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
    ) -> DocumentIR:
        file_path = Path(file_path)
        logger.info(f"Extracting DOCX: {file_path.name}")

        doc = DocxDocument(str(file_path))

        images_dir = file_path.parent / "extracted_images" / file_path.stem
        all_blocks: list[ContentBlock] = []
        page = 1
        image_counter = 0

        for child in doc.element.body.iterchildren():
            tag = child.tag
            if tag == qn("w:p"):
                para = DocxParagraph(child, doc)
                # Page breaks inside this paragraph's runs bump the counter.
                # We count breaks encountered before emitting the paragraph,
                # so content that comes after the break lands on the new page.
                breaks_before = self._count_leading_page_breaks(para)
                page += breaks_before

                block = self._paragraph_block(para, page)
                if block is not None:
                    all_blocks.append(block)

                # Extract any inline images in this paragraph
                img_blocks, image_counter = self._extract_paragraph_images(
                    doc, para, page, images_dir, image_counter, all_blocks
                )
                all_blocks.extend(img_blocks)

                # Account for page breaks that appear after the paragraph text
                breaks_after = self._count_trailing_page_breaks(para, breaks_before)
                page += breaks_after

            elif tag == qn("w:tbl"):
                tbl = DocxTable(child, doc)
                all_blocks.extend(self._table_blocks(tbl, page))

        # Assign sequential indices
        for i, b in enumerate(all_blocks):
            b.position.index = i

        doc_ir = DocumentIR(
            source_file=file_path.name,
            source_format="docx",
            mno=mno,
            release=release,
            doc_type=doc_type,
            content_blocks=all_blocks,
            extraction_metadata={
                "page_count": page,
                "images_dir": (
                    str(images_dir.relative_to(file_path.parent))
                    if images_dir.exists()
                    else None
                ),
            },
        )

        logger.info(
            f"Extracted {file_path.name}: {doc_ir.block_count} blocks "
            f"({len(doc_ir.blocks_by_type(BlockType.HEADING))} headings, "
            f"{len(doc_ir.blocks_by_type(BlockType.PARAGRAPH))} text, "
            f"{len(doc_ir.blocks_by_type(BlockType.TABLE))} tables, "
            f"{len(doc_ir.blocks_by_type(BlockType.IMAGE))} images)"
        )
        return doc_ir

    # ------------------------------------------------------------------
    # Paragraph handling
    # ------------------------------------------------------------------

    def _paragraph_block(
        self, para: DocxParagraph, page: int
    ) -> ContentBlock | None:
        style_name = para.style.name if para.style is not None else ""
        level = self._heading_level(style_name)
        block_type = BlockType.HEADING if level is not None else BlockType.PARAGRAPH

        text = self._text_without_strikes(para)
        if not text:
            return None

        # 1.1: For headings, if the entire title portion (everything before
        # VZ_REQ_) was struck through and only the req-ID survives, the
        # heading is effectively deleted — skip it.
        if level is not None and _VZ_REQ_MARKER in text:
            title_part = text.split(_VZ_REQ_MARKER, 1)[0].strip()
            if not title_part:
                return None

        font = self._paragraph_font(para, level)

        return ContentBlock(
            type=block_type,
            position=Position(page=page, index=0, bbox=None),
            text=text,
            level=level,
            style=style_name,
            font_info=font,
        )

    @staticmethod
    def _text_without_strikes(para: DocxParagraph) -> str:
        """Concatenate run text, skipping any run with strikethrough formatting.

        Handles 1.2 (partial strike in title) and 1.3 (struck paragraph text)
        by operating at run level — only the struck words are dropped; the rest
        of the text is preserved verbatim.
        """
        parts: list[str] = []
        for run in para.runs:
            font = run.font
            if getattr(font, "strike", None) or getattr(font, "double_strike", None):
                continue
            parts.append(run.text or "")
        return "".join(parts).strip()

    @staticmethod
    def _heading_level(style_name: str) -> int | None:
        if not style_name:
            return None
        m = _HEADING_STYLE_RE.match(style_name.strip())
        if not m:
            return None
        try:
            lv = int(m.group(1))
        except ValueError:
            return None
        return lv if 1 <= lv <= 9 else None

    def _paragraph_font(
        self, para: DocxParagraph, level: int | None
    ) -> FontInfo:
        """Synthesize a FontInfo from the first run with real font data.

        Falls back to style-default sizes so profiler clustering still
        works on DOCX-only corpora.
        """
        size = None
        bold = False
        italic = False
        font_name = ""

        for run in para.runs:
            if not run.text or not run.text.strip():
                continue
            if size is None and run.font.size is not None:
                try:
                    size = float(run.font.size.pt)
                except Exception:
                    size = None
            if run.bold:
                bold = True
            if run.italic:
                italic = True
            if not font_name and run.font.name:
                font_name = run.font.name
            if size is not None:
                break

        # FR-33 [D-031]: paragraph-level strikethrough is `any` — if any
        # textful run is struck, treat the whole paragraph as struck.
        # Separate pass so we don't disturb the size/bold/italic/font_name
        # early-exit behavior above.
        strikethrough = False
        for run in para.runs:
            if not run.text or not run.text.strip():
                continue
            font = run.font
            if getattr(font, "strike", None) or getattr(font, "double_strike", None):
                strikethrough = True
                break

        if size is None:
            style_size = self._style_font_size(para)
            if style_size is not None:
                size = style_size

        if size is None:
            size = (
                _HEADING_DEFAULT_SIZE.get(level, _BODY_DEFAULT_SIZE)
                if level is not None
                else _BODY_DEFAULT_SIZE
            )

        if not bold and level is not None:
            bold = True  # heading styles are bold by convention

        text = (para.text or "").strip()
        all_caps = bool(text) and text.isupper() and any(c.isalpha() for c in text)

        return FontInfo(
            size=round(size, 1),
            bold=bold,
            italic=italic,
            font_name=font_name,
            all_caps=all_caps,
            color=0,
            strikethrough=strikethrough,
        )

    @staticmethod
    def _style_font_size(para: DocxParagraph) -> float | None:
        """Walk the style inheritance chain for an explicit font size."""
        style = para.style
        visited = set()
        while style is not None and id(style) not in visited:
            visited.add(id(style))
            try:
                if style.font is not None and style.font.size is not None:
                    return float(style.font.size.pt)
            except Exception:
                pass
            style = getattr(style, "base_style", None)
        return None

    # ------------------------------------------------------------------
    # Table handling
    # ------------------------------------------------------------------

    def _table_blocks(self, tbl: DocxTable, page: int) -> list[ContentBlock]:
        """Return ContentBlocks for *tbl*, handling nested tables.

        If the table contains no nested tables it is emitted as a single
        ContentBlock (original behaviour).  If any cell contains a nested
        table the outer table is treated as a container and its content is
        walked in document order: consecutive paragraph runs are flushed as
        a 1-row/1-column text block, and each nested table is emitted as its
        own block (recursively).  This preserves document order across all
        four cases documented in the extraction spec.
        """
        if not self._has_nested_tables(tbl):
            block = self._flat_table_block(tbl, page)
            return [block] if block is not None else []

        blocks: list[ContentBlock] = []
        for row in tbl.rows:
            for cell in row.cells:
                blocks.extend(self._cell_content_blocks(cell, page))
        return blocks

    def _cell_content_blocks(self, cell, page: int) -> list[ContentBlock]:
        """Walk a cell's direct children in document order.

        Consecutive paragraphs are accumulated and flushed as a 1×1 text
        block when a nested table is encountered (or at end of cell).  Each
        nested table is emitted via _table_blocks() so the recursion handles
        arbitrarily deep nesting.
        """
        p_tag   = qn("w:p")
        tbl_tag = qn("w:tbl")

        blocks: list[ContentBlock] = []
        text_parts: list[str] = []

        def _flush_text() -> None:
            combined = " ".join(t for t in text_parts if t).strip()
            text_parts.clear()
            if not combined:
                return
            blocks.append(ContentBlock(
                type=BlockType.TABLE,
                position=Position(page=page, index=0, bbox=None),
                headers=[combined],
                rows=[],
            ))

        for child in cell._element:
            if child.tag == p_tag:
                para = DocxParagraph(child, cell)
                text = (para.text or "").strip()
                if text:
                    text_parts.append(text)
            elif child.tag == tbl_tag:
                _flush_text()
                nested = DocxTable(child, cell)
                blocks.extend(self._table_blocks(nested, page))

        _flush_text()
        return blocks

    @staticmethod
    def _has_nested_tables(tbl: DocxTable) -> bool:
        """Return True if any cell in *tbl* directly contains a nested table."""
        tbl_tag = qn("w:tbl")
        for row in tbl.rows:
            for cell in row.cells:
                for child in cell._element:
                    if child.tag == tbl_tag:
                        return True
        return False

    def _flat_table_block(self, tbl: DocxTable, page: int) -> ContentBlock | None:
        """Emit a plain (non-nested) table as a single ContentBlock."""
        rows_text: list[list[str]] = []
        for row in tbl.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            rows_text.append(cells)

        if not rows_text:
            return None

        headers   = rows_text[0]
        body_rows = rows_text[1:]

        # Skip degenerate tables — single empty column
        non_empty_headers = [h for h in headers if h]
        total_cells = sum(1 for row in body_rows for c in row if c)
        if len(non_empty_headers) <= 1 and total_cells == 0:
            return None

        return ContentBlock(
            type=BlockType.TABLE,
            position=Position(page=page, index=0, bbox=None),
            headers=headers,
            rows=body_rows,
        )

    # ------------------------------------------------------------------
    # Image handling
    # ------------------------------------------------------------------

    def _extract_paragraph_images(
        self,
        doc,
        para: DocxParagraph,
        page: int,
        images_dir: Path,
        counter: int,
        prior_blocks: list[ContentBlock],
    ) -> tuple[list[ContentBlock], int]:
        blocks: list[ContentBlock] = []
        blip_tag = qn("a:blip")
        embed_attr = qn("r:embed")

        for blip in para._element.iter(blip_tag):
            rel_id = blip.get(embed_attr)
            if not rel_id:
                continue
            rel = doc.part.rels.get(rel_id)
            if rel is None or "image" not in rel.reltype:
                continue

            try:
                image_part = rel.target_part
                blob = image_part.blob
            except Exception as e:
                logger.warning(f"Failed to read image rel {rel_id}: {e}")
                continue

            # Determine extension from the image part
            ext = Path(image_part.partname).suffix.lstrip(".") or "bin"
            # Filter very small decorative images only if dimensions are known
            img_filename = f"p{page}_{counter:03d}.{ext}"
            img_path = images_dir / img_filename
            img_path.parent.mkdir(parents=True, exist_ok=True)
            with open(img_path, "wb") as f:
                f.write(blob)

            surrounding = self._surrounding_text(prior_blocks)
            blocks.append(
                ContentBlock(
                    type=BlockType.IMAGE,
                    position=Position(page=page, index=0, bbox=None),
                    image_path=str(
                        img_path.relative_to(images_dir.parent.parent)
                    ),
                    surrounding_text=surrounding,
                )
            )
            counter += 1

        return blocks, counter

    @staticmethod
    def _surrounding_text(blocks: list[ContentBlock], max_chars: int = 200) -> str:
        texts: list[str] = []
        for b in reversed(blocks):
            if b.type in (BlockType.PARAGRAPH, BlockType.HEADING) and b.text:
                texts.append(b.text[:max_chars])
                if len(texts) >= 2:
                    break
        return " ".join(reversed(texts))

    # ------------------------------------------------------------------
    # Page break tracking
    # ------------------------------------------------------------------

    @staticmethod
    def _count_leading_page_breaks(para: DocxParagraph) -> int:
        """Count page breaks in the paragraph before the first run that emits text.

        Word treats a page break at the start of a paragraph as pushing that
        paragraph's text onto the next page; breaks mid-paragraph are treated
        as trailing (content after the break is on a later page, but the
        paragraph's primary location is where it started).
        """
        count = 0
        br_tag = qn("w:br")
        type_attr = qn("w:type")
        for run in para.runs:
            emitted_text = False
            for child in run._element.iterchildren():
                if child.tag == br_tag and child.get(type_attr) == "page":
                    if not emitted_text:
                        count += 1
                elif child.tag == qn("w:t") and (child.text or "").strip():
                    emitted_text = True
            if emitted_text:
                break
        return count

    def _count_trailing_page_breaks(
        self, para: DocxParagraph, leading: int
    ) -> int:
        """Total page breaks in paragraph minus ones already counted as leading."""
        total = 0
        br_tag = qn("w:br")
        type_attr = qn("w:type")
        for br in para._element.iter(br_tag):
            if br.get(type_attr) == "page":
                total += 1
        return max(0, total - leading)
