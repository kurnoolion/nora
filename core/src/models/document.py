"""Normalized intermediate representation for extracted documents.

Matches the schema defined in TDD Section 5.1.7. All format-specific
extractors produce this common representation, consumed uniformly by
the DocumentProfiler and structural parsers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    EMBEDDED_OBJECT = "embedded_object"


@dataclass
class Position:
    """Location of a content block within the source document."""
    page: int
    index: int  # sequential index across the whole document
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1)


@dataclass
class FontInfo:
    """Font attributes for text-based content blocks.

    Critical for the DocumentProfiler's heading detection — it clusters
    blocks by font size/boldness to derive heading level rules.
    """
    size: float
    bold: bool = False
    italic: bool = False
    font_name: str = ""
    all_caps: bool = False
    color: int = 0  # RGB as integer
    # FR-33 [D-031, D-060]: True if every textful run in the block is struck.
    # Derived; not the only strike signal. Per-run strike is captured on
    # ContentBlock.runs (paragraphs / headings) and ContentBlock.row_runs
    # / header_runs (tables). The parser uses font_info.strikethrough for
    # block-level cascade (drop block + cascade for struck section
    # headings) and uses runs / row_runs for partial-text strike (drop
    # struck spans, keep the rest).
    strikethrough: bool = False


@dataclass
class TextRun:
    """A contiguous span of text within a block with shared formatting [D-060].

    Captured by extractors that have access to per-run formatting (DOCX
    natively; XLSX per cell; PDF coarse — one run per cell/block with the
    block's strike state). Consumers reconstruct "live" text by
    concatenating runs where ``struck=False`` — that's the parser's
    partial-strike path.
    """
    text: str
    struck: bool = False


@dataclass
class MergedCell:
    """A horizontally and/or vertically merged cell inside a table block.

    Captured by the DOCX extractor when a `<w:tc>` carries `<w:gridSpan
    w:val="N">` (horizontal merge) or participates in a `<w:vMerge>`
    chain (vertical merge). The merge ANCHOR cell (the one with
    `gridSpan>1` or the first cell in a `vMerge` chain) is recorded;
    continuation cells are suppressed from `headers`/`rows` to keep
    those matrices rectangular.

    Useful for detection logic that looks for "label-inside-merged-cell"
    layouts — common in revision-history and glossary tables that lack
    an introducing heading paragraph (e.g. row 0 of the table is a
    single merged cell containing the text "Revision History"). The
    text is also concatenated into the joined-headers form by some
    parser scoring paths.
    """
    row: int  # 0 = header row; 1+ = body row index (matches `rows[row-1]`)
    col: int  # 0-based column index of the merge anchor
    rowspan: int = 1
    colspan: int = 1
    text: str = ""


@dataclass
class ContentBlock:
    """A single content block in the normalized intermediate representation."""
    type: BlockType
    position: Position

    # Text content (for heading, paragraph)
    text: str = ""
    level: int | None = None  # heading level (from DOCX styles; None for PDF)
    font_info: FontInfo | None = None  # primary font of the block (PDF extraction)
    style: str = ""  # DOCX style name if available

    # Per-run formatting for paragraphs/headings [D-060]. Empty for
    # legacy IRs and for blocks whose extractor does not provide run-level
    # data — consumers fall back to ``text`` + ``font_info.strikethrough``
    # in that case.
    runs: list[TextRun] = field(default_factory=list)

    # Source line boundaries for paragraph/heading blocks. The PDF extractor
    # groups multiple source lines into one block and ``text`` joins them with
    # spaces; ``lines`` preserves the original per-line split so consumers can
    # tell a heading/title line from the body line(s) beneath it (the join
    # otherwise produces a run-on sentence that blurs the section hierarchy).
    # ``" ".join(lines) == text``. Empty for legacy IRs and extractors that
    # don't surface line structure — consumers fall back to ``text``.
    lines: list[str] = field(default_factory=list)

    # Table content
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    # Per-cell run lists for tables [D-060]. Parallel to ``headers`` /
    # ``rows`` — ``header_runs[c]`` are the runs for header cell c;
    # ``row_runs[r][c]`` are the runs for body cell (r, c). Empty for
    # legacy IRs; consumers fall back to the merged string forms.
    header_runs: list[list[TextRun]] = field(default_factory=list)
    row_runs: list[list[list[TextRun]]] = field(default_factory=list)

    # Merged-cell metadata for tables. Each entry describes one merge
    # anchor; continuation cells are NOT listed (they're suppressed from
    # ``headers`` / ``rows`` to keep those rectangular). Empty for
    # non-merged tables and for extractors that don't surface merges.
    merged_cells: list[MergedCell] = field(default_factory=list)

    # Image content
    image_path: str = ""
    surrounding_text: str = ""

    # Embedded object content
    object_type: str = ""  # "xlsx", "docx", "pdf", etc.
    extracted_path: str = ""
    extracted_content: dict[str, Any] = field(default_factory=dict)

    # Metadata hints for downstream processing
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Strike helpers (D-060)
    # ------------------------------------------------------------------

    def live_text(self) -> str:
        """Return the block's text with struck runs removed.

        For paragraphs / headings. When ``runs`` is empty, falls back to
        ``""`` if the whole block is struck, else ``text`` unchanged.
        """
        if self.runs:
            return "".join(r.text for r in self.runs if not r.struck)
        if self.font_info is not None and self.font_info.strikethrough:
            return ""
        return self.text

    def header_all_struck(self) -> bool:
        """True iff every textful header cell has all its textful runs struck."""
        if not self.header_runs:
            return False
        return _cells_all_struck(self.header_runs)

    def row_all_struck(self, row_index: int) -> bool:
        """True iff every textful cell in the row is fully struck."""
        if not (0 <= row_index < len(self.row_runs)):
            return False
        return _cells_all_struck(self.row_runs[row_index])

    def cell_live_text(self, row_index: int, col_index: int) -> str:
        """Return the body cell's text with struck runs removed."""
        if 0 <= row_index < len(self.row_runs):
            cell = self.row_runs[row_index]
            if 0 <= col_index < len(cell):
                runs = cell[col_index]
                return "".join(r.text for r in runs if not r.struck)
        # Fallback to the merged-string row matrix
        if 0 <= row_index < len(self.rows):
            row = self.rows[row_index]
            if 0 <= col_index < len(row):
                return row[col_index]
        return ""

    def header_live_text(self, col_index: int) -> str:
        """Return the header cell's text with struck runs removed."""
        if 0 <= col_index < len(self.header_runs):
            return "".join(r.text for r in self.header_runs[col_index] if not r.struck)
        if 0 <= col_index < len(self.headers):
            return self.headers[col_index]
        return ""

    def last_run_text(self) -> str:
        """Return the text of the last ``TextRun`` (regardless of strike state).

        Used by the parser's ``anchor="last_run"`` req_id extraction path:
        in run-aware corpora the requirement_id is conventionally the
        trailing run of a heading, so reading ``runs[-1]`` is more
        precise than regex-searching the full text. Returns ``""`` when
        ``runs`` is empty. Strike state is intentionally ignored — the
        caller decides whether a struck last-run should still produce a
        cascade-relevant id (it should: struck reqs are recorded in the
        ``struck_req_ids`` set so table-anchored extraction skips them).
        """
        if not self.runs:
            return ""
        return self.runs[-1].text


def _cells_all_struck(cells: list[list[TextRun]]) -> bool:
    """True iff every cell with textful content has every textful run struck.

    Empty cells (no textful runs) are ignored — they can't be "struck" or
    "unstruck"; they just don't contribute to the decision.
    """
    saw_textful_cell = False
    for cell_runs in cells:
        textful = [r for r in cell_runs if r.text and r.text.strip()]
        if not textful:
            continue
        saw_textful_cell = True
        if not all(r.struck for r in textful):
            return False
    return saw_textful_cell


@dataclass
class DocumentIR:
    """Normalized intermediate representation for a single document.

    This is the output of the extraction layer and input to both
    the DocumentProfiler and the structural parser.
    """
    source_file: str
    source_format: str  # "pdf", "doc", "docx", "xls", "xlsx"
    mno: str = ""
    release: str = ""
    doc_type: str = ""  # "requirement", "testcase"
    plan: str = ""  # plan name when the input layout carries it (e.g. a per-plan
    # directory under input/<MNO>/<release>/<plan>/); empty for flat layouts.
    content_blocks: list[ContentBlock] = field(default_factory=list)
    extraction_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        if not self.content_blocks:
            return 0
        return max(b.position.page for b in self.content_blocks)

    @property
    def block_count(self) -> int:
        return len(self.content_blocks)

    def blocks_by_type(self, block_type: BlockType) -> list[ContentBlock]:
        return [b for b in self.content_blocks if b.type == block_type]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)

    def save_json(self, path: Path) -> None:
        """Save the IR to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> DocumentIR:
        """Load an IR from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        blocks = []
        for b in data.get("content_blocks", []):
            pos_data = b["position"]
            bbox_raw = pos_data.get("bbox")
            pos = Position(
                page=pos_data["page"],
                index=pos_data["index"],
                bbox=tuple(bbox_raw) if bbox_raw is not None else None,
            )
            font = FontInfo(**b["font_info"]) if b.get("font_info") else None
            runs = [TextRun(**r) for r in b.get("runs", [])]
            header_runs = [
                [TextRun(**r) for r in cell] for cell in b.get("header_runs", [])
            ]
            row_runs = [
                [[TextRun(**r) for r in cell] for cell in row]
                for row in b.get("row_runs", [])
            ]
            merged_cells = [MergedCell(**mc) for mc in b.get("merged_cells", [])]
            blocks.append(ContentBlock(
                type=BlockType(b["type"]),
                position=pos,
                text=b.get("text", ""),
                level=b.get("level"),
                font_info=font,
                style=b.get("style", ""),
                runs=runs,
                headers=b.get("headers", []),
                rows=b.get("rows", []),
                header_runs=header_runs,
                row_runs=row_runs,
                merged_cells=merged_cells,
                image_path=b.get("image_path", ""),
                surrounding_text=b.get("surrounding_text", ""),
                object_type=b.get("object_type", ""),
                extracted_path=b.get("extracted_path", ""),
                extracted_content=b.get("extracted_content", {}),
                metadata=b.get("metadata", {}),
            ))
        return cls(
            source_file=data["source_file"],
            source_format=data["source_format"],
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            doc_type=data.get("doc_type", ""),
            plan=data.get("plan", ""),
            content_blocks=blocks,
            extraction_metadata=data.get("extraction_metadata", {}),
        )
