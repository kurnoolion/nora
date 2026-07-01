"""XLSX content extractor using openpyxl.

Produces the normalized intermediate representation (TDD 5.1.7) from XLSX
files. Each worksheet becomes a section: a heading block (with the sheet
name) followed by a table block (first row as headers, remaining rows as
body cells). Page numbers track the sheet index (1-based).

Per FR-1: PDF / DOCX / XLSX are the v1 input formats. XLS is deferred (FR-27,
D-018).
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

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


# Default font sizes used to feed the profiler's font-clustering heading
# detector. XLSX has no native heading metadata, so we synthesize a header
# size for sheet titles vs body cells.
_HEADING_FONT_SIZE = 14.0
_BODY_FONT_SIZE = 11.0


def _cell_text(value) -> str:
    """Convert a cell value to a normalized stripped string ("" for None)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _cell_to_runs(c) -> list[TextRun]:
    """Build a single-run TextRun list for an XLSX cell [D-060].

    XLSX rich-text-with-mixed-strike inside a single cell is rare and
    not exercised by current corpora — capture the cell as one run with
    the cell's font.strike state.
    """
    text = _cell_text(c.value)
    if not text:
        return []
    font = getattr(c, "font", None)
    struck = bool(font is not None and getattr(font, "strike", False))
    return [TextRun(text=text, struck=struck)]


def _row_all_struck(cells) -> bool:
    """Return True if every non-empty cell in `cells` has font.strike=True.

    Per D-031: an XLSX row is treated as "struck" only when ALL non-empty
    cells in it are struck. Partial strike is treated as in-cell editing,
    not row-deletion.
    """
    has_any_text = False
    for c in cells:
        text = _cell_text(c.value)
        if not text:
            continue
        has_any_text = True
        font = getattr(c, "font", None)
        if font is None or not getattr(font, "strike", False):
            return False
    return has_any_text  # all-empty row is not "all struck"


class XLSXExtractor(BaseExtractor):
    """Extract worksheets and tables from XLSX files (FR-1).

    FR-33 [D-031, D-060]: cells with `cell.font.strike` are honored.
    Per-row strike state is captured in `row_runs` (every cell's runs
    carry a `struck` flag); whole-row strike is computed by the parser
    via ``ContentBlock.row_all_struck(i)``. Header strike is captured in
    ``header_runs``. Whole-table strike (header + every body row struck)
    is signalled via ``font_info.strikethrough`` so the parser's
    existing FR-33 cascade drops the block. **No row content is
    dropped at extract time** [D-060] — the IR keeps everything;
    parser/UI decide what to do.
    """

    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
        detect_text_tables: bool = False,  # PDF-only hint; ignored here
        header_footer_margin_mode: str = "blanket",  # PDF-only hint; ignored here
    ) -> DocumentIR:
        file_path = Path(file_path)
        logger.info(f"Extracting XLSX: {file_path.name}")

        # read_only=False: we need cell.font.strike for FR-33; read-only
        # mode strips font info from ReadOnlyCell. Trade-off is acceptable
        # for telecom-spec XLSX sizes (typically <50 MB).
        wb = openpyxl.load_workbook(str(file_path), data_only=True)
        sheet_count = len(wb.worksheets)

        all_blocks: list[ContentBlock] = []
        block_index = 0
        total_struck_rows = 0

        for page_num, ws in enumerate(wb.worksheets, start=1):
            # Iterate as Cell objects (not values_only) so cell.font.strike
            # is accessible for the FR-33 row-drop check.
            rows = [list(row) for row in ws.iter_rows()]
            # Skip wholly-empty sheets
            non_empty = [r for r in rows if any(_cell_text(c.value) for c in r)]
            if not non_empty:
                continue

            # Heading block: sheet name (sheet titles are not formatted text;
            # they cannot be struck — strikethrough stays False).
            heading = ContentBlock(
                type=BlockType.HEADING,
                position=Position(page=page_num, index=block_index),
                text=ws.title,
                level=1,
                font_info=FontInfo(size=_HEADING_FONT_SIZE, bold=True),
                style="SheetTitle",
            )
            all_blocks.append(heading)
            block_index += 1

            # First row = headers. D-060: keep the row in the IR; record
            # per-cell strike via `header_runs`. The header row's overall
            # strike state participates in whole-table strike below.
            header_cells = non_empty[0]
            headers = [_cell_text(c.value) for c in header_cells]
            header_runs = [_cell_to_runs(c) for c in header_cells]
            header_struck = _row_all_struck(header_cells)

            # Body rows: D-060: keep all rows in the IR; mark per-cell
            # strike via `row_runs`. The parser drops fully-struck rows
            # at parse time when `profile.ignore_strikeout` is True
            # (default), via ``ContentBlock.row_all_struck(i)``.
            body: list[list[str]] = []
            row_runs: list[list[list[TextRun]]] = []
            sheet_struck_rows = 0
            for row_cells in non_empty[1:]:
                body.append([_cell_text(c.value) for c in row_cells])
                row_runs.append([_cell_to_runs(c) for c in row_cells])
                if _row_all_struck(row_cells):
                    sheet_struck_rows += 1
            total_struck_rows += sheet_struck_rows

            # Whole-table strike: header AND every body row struck →
            # font_info.strikethrough so the parser cascades. Header
            # alone → also whole-table struck (mirrors prior behavior).
            all_body_struck = bool(body) and all(
                _row_all_struck(rc) for rc in non_empty[1:]
            )
            table_struck = header_struck and (not body or all_body_struck)
            # Backward-compat: previously a struck header alone marked
            # the table struck. Preserve that — a struck header ⇒
            # cascade-drop the table.
            if header_struck:
                table_struck = True

            table = ContentBlock(
                type=BlockType.TABLE,
                position=Position(page=page_num, index=block_index),
                headers=headers,
                rows=body,
                header_runs=header_runs,
                row_runs=row_runs,
                font_info=FontInfo(
                    size=_BODY_FONT_SIZE,
                    strikethrough=table_struck,
                ),
                metadata={
                    "sheet_name": ws.title,
                    "row_count": len(body),
                    # Diagnostic counter — rows are no longer dropped at
                    # extract time (D-060), but the count of *fully
                    # struck* rows is still useful for the compact RPT.
                    "struck_rows_marked": sheet_struck_rows,
                },
            )
            all_blocks.append(table)
            block_index += 1

        wb.close()

        return DocumentIR(
            source_file=str(file_path),
            source_format="xlsx",
            mno=mno,
            release=release,
            doc_type=doc_type,
            content_blocks=all_blocks,
            extraction_metadata={
                "sheet_count": sheet_count,
                "struck_xlsx_rows_marked": total_struck_rows,
            },
        )
