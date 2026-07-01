"""Extractor fusion of a LayoutProvider's tables/figures into the pymupdf text
flow (mno-c-ingestion). Uses a synthetic PDF + a fake provider — no Docling/models.

Verifies the fusion contract: provider tables/figures become TABLE/IMAGE blocks
carrying html/caption, their bboxes suppress the pymupdf paragraphs beneath them,
and text outside those regions is preserved.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fitz")            # pymupdf
pytest.importorskip("pdfplumber")

from core.src.extraction import pdf_extractor as pe
from core.src.extraction.layout_provider import (
    LayoutFigure,
    LayoutStructures,
    LayoutTable,
)
from core.src.models.document import BlockType

_HTML = "<table><tr><th>H</th></tr><tr><td>X</td></tr></table>"


class _FakeProvider:
    name = "fake"

    def available(self):
        return True, "fake"

    def extract_layout(self, pdf_path, image_dir=None, want_table_grid=True):
        grid = (["H"], [["X"]]) if want_table_grid else ([], [])
        return LayoutStructures(
            tables=[LayoutTable(page=1, bbox=(30, 135, 270, 165), html=_HTML,
                                headers=grid[0], rows=grid[1])],
            figures=[LayoutFigure(page=1, bbox=(30, 320, 200, 360),
                                  image_path="", caption="Figure Z")],
            page_sizes={1: (300.0, 400.0)})


@pytest.fixture()
def synth_pdf(tmp_path):
    import fitz
    doc = fitz.open()
    pg = doc.new_page(width=300, height=400)
    pg.insert_text((40, 110), "1 Requirements", fontsize=14)     # heading (below margin, above table)
    pg.insert_text((40, 150), "inside table cell", fontsize=10)  # inside table bbox
    pg.insert_text((40, 300), "outside body text", fontsize=10)  # below table, above fig
    path = tmp_path / "synth.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _extract(pdf, monkeypatch, provider_table_grid=True):
    monkeypatch.setattr(
        pe, "get_layout_provider",
        lambda name: _FakeProvider() if name == "fake" else None)
    return pe.PDFExtractor().extract(
        pdf, layout_provider="fake", provider_table_grid=provider_table_grid)


def test_provider_table_becomes_html_block(synth_pdf, monkeypatch):
    ir = _extract(synth_pdf, monkeypatch)
    tables = ir.blocks_by_type(BlockType.TABLE)
    assert len(tables) == 1
    assert tables[0].html == _HTML
    assert tables[0].headers == ["H"] and tables[0].rows == [["X"]]


def test_paragraph_under_table_is_suppressed(synth_pdf, monkeypatch):
    ir = _extract(synth_pdf, monkeypatch)
    para_text = " ".join(b.text for b in ir.blocks_by_type(BlockType.PARAGRAPH))
    assert "inside table cell" not in para_text     # suppressed by the table bbox
    assert "outside body text" in para_text          # outside the table → kept
    assert "Requirements" in para_text               # heading above the table → kept


def test_ir_table_grid_present_by_default(synth_pdf, monkeypatch):
    t = _extract(synth_pdf, monkeypatch, provider_table_grid=True) \
        .blocks_by_type(BlockType.TABLE)[0]
    assert t.html == _HTML and t.headers == ["H"] and t.rows == [["X"]]


def test_ir_table_grid_omitted_when_disabled(synth_pdf, monkeypatch):
    # provider_table_grid=False → HTML only, no redundant flat grid in the IR.
    t = _extract(synth_pdf, monkeypatch, provider_table_grid=False) \
        .blocks_by_type(BlockType.TABLE)[0]
    assert t.html == _HTML and t.headers == [] and t.rows == []


def test_provider_figure_becomes_image_block_with_caption(synth_pdf, monkeypatch):
    ir = _extract(synth_pdf, monkeypatch)
    imgs = ir.blocks_by_type(BlockType.IMAGE)
    assert len(imgs) == 1
    assert imgs[0].caption == "Figure Z"


def test_reading_order_table_between_heading_and_body(synth_pdf, monkeypatch):
    ir = _extract(synth_pdf, monkeypatch)
    # blocks are sorted by (page, y); the table (y=135) sits after the heading
    # (y~40) and before the outside body (y~300).
    kinds = [(b.type, b.position.bbox[1] if b.position.bbox else 9999)
             for b in ir.content_blocks]
    tbl_y = next(y for t, y in kinds if t == BlockType.TABLE)
    body_y = next(y for t, y in kinds
                  if t == BlockType.PARAGRAPH and y > 200)
    assert tbl_y < body_y
