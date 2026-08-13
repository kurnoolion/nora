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
from html import escape
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
    MergedCell,
    Position,
    TextRun,
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


def _render_merged_table_html(
    headers: list[str],
    rows: list[list[str]],
    merged_cells: list[MergedCell],
) -> str:
    """Render the rectangular grid + merge anchors as lossless table HTML.

    The flat ``headers``/``rows`` matrices blank out merge-continuation
    positions, so a markdown render flattens merged regions into empty
    strings and the rowspan/colspan semantics never reach the corpus.
    This re-encodes them as HTML span attributes — the same shape the
    layout-provider (Docling) path emits — so the parser's html-preferred
    inline rule picks it up with no parser change. Continuation positions
    are omitted from the output (their content lives on the anchor).

    ``merged_cells`` row indices are full-table coordinates (row 0 = the
    header row), matching ``ContentBlock.merged_cells``.
    """
    covered: set[tuple[int, int]] = set()
    anchors: dict[tuple[int, int], MergedCell] = {}
    for m in merged_cells:
        anchors[(m.row, m.col)] = m
        for r in range(m.row, m.row + m.rowspan):
            for c in range(m.col, m.col + m.colspan):
                if (r, c) != (m.row, m.col):
                    covered.add((r, c))

    parts = ["<table>"]
    for r, grid_row in enumerate([headers] + rows):
        tag = "th" if r == 0 else "td"
        cells = []
        for c, cell in enumerate(grid_row):
            if (r, c) in covered:
                continue
            attrs = ""
            m = anchors.get((r, c))
            if m is not None:
                if m.rowspan > 1:
                    attrs += f' rowspan="{m.rowspan}"'
                if m.colspan > 1:
                    attrs += f' colspan="{m.colspan}"'
            cells.append(f"<{tag}{attrs}>{escape(cell or '')}</{tag}>")
        parts.append("<tr>" + "".join(cells) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


class DOCXExtractor(BaseExtractor):
    """Extract paragraphs, tables, and images from DOCX files."""

    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
        detect_text_tables: bool = False,  # PDF-only hint; ignored here
        header_footer_margin_mode: str = "blanket",  # PDF-only hint; ignored here
        layout_provider: str = "",  # PDF-only hint; ignored here
        provider_table_grid: bool = True,  # PDF-only hint; ignored here
        images_root: "Path | None" = None,
    ) -> DocumentIR:
        file_path = Path(file_path)
        logger.info(f"Extracting DOCX: {file_path.name}")

        doc = DocxDocument(str(file_path))

        # images_root keeps image artifacts in the BUILD output (the input
        # corpus may be a read-only mount); legacy next-to-source otherwise.
        images_dir = (
            Path(images_root) / file_path.stem if images_root
            else file_path.parent / "extracted_images" / file_path.stem
        )
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
                block = self._table_block(tbl, page)
                if block is not None:
                    all_blocks.append(block)
                # Walk for tables nested inside cells. Word docs commonly
                # use an outer 1×1 wrapper around a real content table
                # (glossary, etc.); the wrapper's `cell.text` only sees
                # paragraph text, so the nested table would otherwise be
                # silently lost. Recurse via `cell.tables`; emit each
                # nested table as its own TABLE block in document order
                # (parent first, then children). The recursion runs even
                # when the outer block was dropped — a pure layout
                # wrapper is empty by intent and the nested content is
                # still the real signal.
                for nested in self._iter_nested_tables(tbl):
                    nested_block = self._table_block(nested, page)
                    if nested_block is not None:
                        all_blocks.append(nested_block)

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
                    str(images_dir.relative_to(images_dir.parent.parent))
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
        text = (para.text or "").strip()
        if not text:
            return None

        style_name = para.style.name if para.style is not None else ""
        level = self._heading_level(style_name)
        block_type = BlockType.HEADING if level is not None else BlockType.PARAGRAPH
        font = self._paragraph_font(para, level)
        runs = self._paragraph_runs(para)
        # Whole-block strike [D-060]: True iff every textful run is struck.
        # When at least one textful run is non-struck (partial strike), the
        # font_info flag is False — the parser uses `runs` for the
        # span-level drop instead. Override unconditionally so we replace
        # `_paragraph_font`'s legacy "any-run-struck" coarse heuristic.
        if runs:
            textful = [r for r in runs if r.text and r.text.strip()]
            font.strikethrough = bool(textful) and all(r.struck for r in textful)

        return ContentBlock(
            type=block_type,
            position=Position(page=page, index=0, bbox=None),
            text=text,
            level=level,
            style=style_name,
            font_info=font,
            runs=runs,
        )

    @staticmethod
    def _paragraph_runs(para: DocxParagraph) -> list[TextRun]:
        """Build a TextRun list preserving per-run strike state [D-060].

        Inter-run whitespace (e.g. between two consecutive runs that
        python-docx reports as separate run elements with text "foo" and
        "bar") is preserved verbatim — the joined text matches
        ``para.text``. Empty-text runs are dropped.
        """
        runs: list[TextRun] = []
        for run in para.runs:
            t = run.text or ""
            if not t:
                continue
            font = run.font
            struck = bool(
                getattr(font, "strike", None) or getattr(font, "double_strike", None)
            )
            runs.append(TextRun(text=t, struck=struck))
        return runs

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

    def _table_block(self, tbl: DocxTable, page: int) -> ContentBlock | None:
        # Walk every (row, col) cell position. python-docx's row.cells
        # returns the SAME Cell object (same underlying ``CT_Tc`` element)
        # for every (row, col) position inside a merged region — we use
        # that element identity to find merge anchors and record
        # continuation positions as empty in the rectangular matrix.
        # Hash by the CT_Tc element itself (hashable, identity-stable
        # across the lifetime of the open document).
        seen_tcs: dict = {}
        rows_text: list[list[str]] = []
        rows_runs: list[list[list[TextRun]]] = []

        for r_idx, row in enumerate(tbl.rows):
            cells_text: list[str] = []
            cells_runs: list[list[TextRun]] = []
            for c_idx, c in enumerate(row.cells):
                tc = c._tc
                if tc in seen_tcs:
                    anchor = seen_tcs[tc]
                    anchor["colspan"] = max(
                        anchor["colspan"], c_idx - anchor["col"] + 1,
                    )
                    anchor["rowspan"] = max(
                        anchor["rowspan"], r_idx - anchor["row"] + 1,
                    )
                    cells_text.append("")
                    cells_runs.append([])
                else:
                    text = (c.text or "").strip()
                    runs = self._cell_runs(c)
                    seen_tcs[tc] = {
                        "row": r_idx, "col": c_idx,
                        "rowspan": 1, "colspan": 1,
                        "text": text,
                    }
                    cells_text.append(text)
                    cells_runs.append(runs)
            rows_text.append(cells_text)
            rows_runs.append(cells_runs)

        if not rows_text:
            return None

        headers = rows_text[0]
        body_rows = rows_text[1:]
        header_runs = rows_runs[0]
        body_row_runs = rows_runs[1:]

        # Drop only when every cell across headers + body is empty.
        # The previous shape (`len(non_empty_headers) <= 1 and total_cells
        # == 0`) accidentally dropped 1×1 content tables — Word docs
        # commonly use single-cell tables as paragraph containers, and
        # the prior filter silently lost them on extract.
        non_empty_headers = sum(1 for h in headers if h)
        non_empty_body = sum(1 for row in body_rows for c in row if c)
        if non_empty_headers == 0 and non_empty_body == 0:
            return None

        # Build merged_cells list — only entries that actually span >1.
        merged_cells = [
            MergedCell(
                row=info["row"], col=info["col"],
                rowspan=info["rowspan"], colspan=info["colspan"],
                text=info["text"],
            )
            for info in seen_tcs.values()
            if info["rowspan"] > 1 or info["colspan"] > 1
        ]
        merged_cells.sort(key=lambda m: (m.row, m.col))

        block = ContentBlock(
            type=BlockType.TABLE,
            position=Position(page=page, index=0, bbox=None),
            headers=headers,
            rows=body_rows,
            header_runs=header_runs,
            row_runs=body_row_runs,
            merged_cells=merged_cells,
        )

        # Merged-cell tables render lossless HTML into block.html (strand
        # table-fidelity): the flat grid blanks continuation cells, so the
        # markdown inline flattens merged regions — rowspan/colspan only
        # survive via HTML. Gated on merges present (the ~97% unmerged
        # tables stay markdown — cheaper in enrichment prompts) AND on the
        # table carrying no struck runs: strike drops are parser-side and
        # profile-gated (D-060), and the parser's html-preferred inline
        # rule would resurrect struck rows baked into the HTML. Struck
        # merged tables fall back to the markdown flatten, as before.
        any_struck = any(
            run.struck for cell in header_runs for run in cell
        ) or any(
            run.struck
            for row in body_row_runs
            for cell in row
            for run in cell
        )
        if merged_cells and not any_struck:
            block.html = _render_merged_table_html(
                headers, body_rows, merged_cells
            )

        # Whole-table strike [D-060]: header AND every body row fully
        # struck → mark the block so the parser's existing cascade
        # (font_info.strikethrough check) drops the table.
        whole_struck = (
            block.header_all_struck()
            and bool(body_row_runs)
            and all(block.row_all_struck(i) for i in range(len(body_row_runs)))
        )
        if whole_struck:
            block.font_info = FontInfo(size=0.0, strikethrough=True)
        return block

    def _iter_nested_tables(self, tbl: DocxTable):
        """Yield every table nested inside *tbl*'s cells, recursing for
        arbitrary depth. Dedupes merged regions via ``_tc`` identity so
        a horizontally-merged cell isn't visited per-column.

        Order: depth-first, document order within each cell — matches
        the order a reader would encounter the nested content.
        """
        seen_tcs: set[int] = set()
        for row in tbl.rows:
            for cell in row.cells:
                tc_id = id(cell._tc)
                if tc_id in seen_tcs:
                    continue
                seen_tcs.add(tc_id)
                for inner in cell.tables:
                    yield inner
                    yield from self._iter_nested_tables(inner)

    @staticmethod
    def _cell_runs(cell) -> list[TextRun]:
        """Flatten every paragraph's runs in a table cell into a single run list.

        Paragraph boundaries inside the cell are preserved as inter-run
        whitespace where the source had it; empty-text runs are dropped.
        Same per-run strike rule as paragraph runs (D-060).
        """
        runs: list[TextRun] = []
        for para in cell.paragraphs:
            for run in para.runs:
                t = run.text or ""
                if not t:
                    continue
                font = run.font
                struck = bool(
                    getattr(font, "strike", None) or getattr(font, "double_strike", None)
                )
                runs.append(TextRun(text=t, struck=struck))
        return runs

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
