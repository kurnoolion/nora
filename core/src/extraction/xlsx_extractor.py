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


class XLSXExtractor(BaseExtractor):
    """Extract worksheets and tables from XLSX files (FR-1)."""

    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
    ) -> DocumentIR:
        file_path = Path(file_path)
        logger.info(f"Extracting XLSX: {file_path.name}")

        wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
        sheet_count = len(wb.worksheets)

        all_blocks: list[ContentBlock] = []
        block_index = 0

        for page_num, ws in enumerate(wb.worksheets, start=1):
            rows = list(ws.iter_rows(values_only=True))
            # Skip wholly-empty sheets
            non_empty = [r for r in rows if any(_cell_text(c) for c in r)]
            if not non_empty:
                continue

            # Heading block: sheet name
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

            # Table block: first row as headers, remaining rows as body
            headers = [_cell_text(c) for c in non_empty[0]]
            body = [[_cell_text(c) for c in row] for row in non_empty[1:]]

            table = ContentBlock(
                type=BlockType.TABLE,
                position=Position(page=page_num, index=block_index),
                headers=headers,
                rows=body,
                font_info=FontInfo(size=_BODY_FONT_SIZE),
                metadata={"sheet_name": ws.title, "row_count": len(body)},
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
            extraction_metadata={"sheet_count": sheet_count},
        )
