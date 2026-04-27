"""Tests for XLSXExtractor (FR-1)."""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip the whole module if openpyxl isn't available (matches the pattern used
# for test_pipeline.py + pymupdf).
openpyxl = pytest.importorskip("openpyxl")

from core.src.extraction.registry import (  # noqa: E402
    extract_document,
    get_extractor,
    supported_extensions,
)
from core.src.extraction.xlsx_extractor import XLSXExtractor  # noqa: E402
from core.src.models.document import BlockType, DocumentIR  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — synthetic XLSX files written to a tmp_path
# ---------------------------------------------------------------------------


def _write_xlsx(path: Path, sheets: dict[str, list[list]]) -> Path:
    """Write a minimal XLSX with the given sheet name -> rows mapping."""
    wb = openpyxl.Workbook()
    # openpyxl creates a default sheet; replace its name and rows for the
    # first item, append fresh sheets for the rest.
    default_ws = wb.active
    sheet_items = list(sheets.items())

    if not sheet_items:
        wb.save(str(path))
        return path

    first_name, first_rows = sheet_items[0]
    default_ws.title = first_name
    for row in first_rows:
        default_ws.append(row)

    for name, rows in sheet_items[1:]:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)

    wb.save(str(path))
    return path


@pytest.fixture
def simple_xlsx(tmp_path: Path) -> Path:
    return _write_xlsx(
        tmp_path / "requirements.xlsx",
        {
            "Cover": [
                ["Field", "Value"],
                ["Title", "VZW LTE Data Retry"],
                ["Release", "Feb2026"],
            ],
            "Requirements": [
                ["ID", "Description", "Category"],
                ["VZ_REQ_LTEDATARETRY_0001", "T3402 timer baseline", "timer"],
                ["VZ_REQ_LTEDATARETRY_0002", "Attach reject handling", "attach"],
            ],
        },
    )


@pytest.fixture
def empty_sheet_xlsx(tmp_path: Path) -> Path:
    return _write_xlsx(
        tmp_path / "with_empty.xlsx",
        {
            "Sheet1": [["a", "b"], ["1", "2"]],
            "EmptySheet": [],
            "Sheet3": [["x"], ["y"]],
        },
    )


# ---------------------------------------------------------------------------
# Direct extractor tests
# ---------------------------------------------------------------------------


def test_extract_simple(simple_xlsx: Path):
    ir = XLSXExtractor().extract(simple_xlsx, mno="VZW", release="Feb2026")

    assert isinstance(ir, DocumentIR)
    assert ir.source_format == "xlsx"
    assert ir.mno == "VZW"
    assert ir.release == "Feb2026"
    assert ir.extraction_metadata["sheet_count"] == 2

    # Two sheets × (1 heading + 1 table) = 4 blocks
    assert ir.block_count == 4
    assert ir.page_count == 2

    headings = ir.blocks_by_type(BlockType.HEADING)
    tables = ir.blocks_by_type(BlockType.TABLE)
    assert len(headings) == 2
    assert len(tables) == 2
    assert headings[0].text == "Cover"
    assert headings[1].text == "Requirements"


def test_table_headers_and_rows(simple_xlsx: Path):
    ir = XLSXExtractor().extract(simple_xlsx)
    tables = ir.blocks_by_type(BlockType.TABLE)

    cover_table = tables[0]
    assert cover_table.headers == ["Field", "Value"]
    assert cover_table.rows == [
        ["Title", "VZW LTE Data Retry"],
        ["Release", "Feb2026"],
    ]
    assert cover_table.metadata["sheet_name"] == "Cover"
    assert cover_table.metadata["row_count"] == 2

    reqs_table = tables[1]
    assert reqs_table.headers == ["ID", "Description", "Category"]
    assert reqs_table.rows[0] == [
        "VZ_REQ_LTEDATARETRY_0001",
        "T3402 timer baseline",
        "timer",
    ]


def test_block_indices_are_contiguous(simple_xlsx: Path):
    ir = XLSXExtractor().extract(simple_xlsx)
    indices = [b.position.index for b in ir.content_blocks]
    assert indices == list(range(len(ir.content_blocks)))


def test_skips_empty_sheets(empty_sheet_xlsx: Path):
    ir = XLSXExtractor().extract(empty_sheet_xlsx)
    # Non-empty sheets: Sheet1, Sheet3 — so 2 headings + 2 tables = 4 blocks
    assert ir.block_count == 4
    headings = ir.blocks_by_type(BlockType.HEADING)
    titles = [h.text for h in headings]
    assert "Sheet1" in titles
    assert "Sheet3" in titles
    assert "EmptySheet" not in titles
    # sheet_count records all worksheets, including the empty one
    assert ir.extraction_metadata["sheet_count"] == 3


def test_handles_non_string_cells(tmp_path: Path):
    path = _write_xlsx(
        tmp_path / "mixed.xlsx",
        {"Data": [["int", "float", "bool"], [42, 3.14, True]]},
    )
    ir = XLSXExtractor().extract(path)
    table = ir.blocks_by_type(BlockType.TABLE)[0]
    assert table.rows == [["42", "3.14", "True"]]


# ---------------------------------------------------------------------------
# Registry integration tests
# ---------------------------------------------------------------------------


def test_xlsx_registered_as_supported_extension():
    assert ".xlsx" in supported_extensions()


def test_get_extractor_returns_xlsx_for_xlsx_file(tmp_path: Path):
    xlsx_path = _write_xlsx(tmp_path / "test.xlsx", {"S": [["a"]]})
    extractor = get_extractor(xlsx_path)
    assert isinstance(extractor, XLSXExtractor)


def test_extract_document_via_registry(simple_xlsx: Path):
    ir = extract_document(simple_xlsx, mno="VZW", release="Feb2026", doc_type="requirement")
    assert ir.source_format == "xlsx"
    assert ir.mno == "VZW"
    assert ir.doc_type == "requirement"
    assert ir.block_count == 4


# ---------------------------------------------------------------------------
# Round-trip via DocumentIR.save_json / load_json
# ---------------------------------------------------------------------------


def test_ir_round_trip(simple_xlsx: Path, tmp_path: Path):
    ir = XLSXExtractor().extract(simple_xlsx, mno="VZW")
    out_path = tmp_path / "ir.json"
    ir.save_json(out_path)
    loaded = DocumentIR.load_json(out_path)
    assert loaded.source_format == "xlsx"
    assert loaded.block_count == ir.block_count
    assert loaded.blocks_by_type(BlockType.TABLE)[0].headers == ["Field", "Value"]
