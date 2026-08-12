"""Background-band rect filter (strand mno-b-tables).

Some corpora paint every page with full-bleed, fill-only rectangles (header /
content / footer bands). pdfplumber's "lines" table strategy treats rect edges
as table rules: when a real ruled table shares its x-extent with the content
band, the band's edges join the table's lattice and the detected bbox spans
the whole band — and the extractor's overlap suppression then swallows every
paragraph on the page into the phantom table. `_drop_background_rects` removes
fill-only, stroke-less rects >= half the page area before table detection.

Unit tests cover the filter predicate on a fake page; the integration tests
build a real PDF with pymupdf and run it through pdfplumber + PDFExtractor.
No corpus content or geometry is embedded — the fixture is fully synthetic.
"""

from __future__ import annotations

import fitz  # pymupdf
import pdfplumber
import pytest

from core.src.extraction.pdf_extractor import PDFExtractor, _drop_background_rects
from core.src.models.document import BlockType


def _rect(w: float, h: float, fill: bool = True, stroke: bool = False,
          kind: str = "rect") -> dict:
    return {"object_type": kind, "fill": fill, "stroke": stroke,
            "width": float(w), "height": float(h)}


class _FakePage:
    def __init__(self, rects: list[dict], w: float = 612.0, h: float = 792.0,
                 curves: list[dict] | None = None):
        self.rects = rects
        self.curves = curves or []
        self.width = w
        self.height = h

    def filter(self, keep):
        return _FakePage([r for r in self.rects if keep(r)], self.width,
                         self.height, [c for c in self.curves if keep(c)])


class TestDropBackgroundRects:
    def test_giant_fill_only_rect_dropped(self):
        page = _FakePage([_rect(540, 620), _rect(100, 20)])  # band + normal rect
        out = _drop_background_rects(page)
        assert out is not page
        assert [r["width"] for r in out.rects] == [100]

    def test_no_giant_rect_is_identity_noop(self):
        # Corpora without bands must take the exact same code path as before.
        page = _FakePage([_rect(100, 20), _rect(200, 50, stroke=True)])
        assert _drop_background_rects(page) is page

    def test_giant_stroked_rect_kept(self):
        # A stroked rect is genuine ruling (e.g. a page-sized outer border).
        page = _FakePage([_rect(540, 620, stroke=True)])
        assert _drop_background_rects(page) is page

    def test_giant_unfilled_rect_kept(self):
        page = _FakePage([_rect(540, 620, fill=False)])
        assert _drop_background_rects(page) is page

    def test_just_under_threshold_kept(self):
        # 612x792 page => threshold 242352 pt^2; 540x440 = 237600 < threshold.
        page = _FakePage([_rect(540, 440)])
        assert _drop_background_rects(page) is page

    def test_zero_area_page_noop(self):
        page = _FakePage([_rect(540, 620)], w=0.0, h=0.0)
        assert _drop_background_rects(page) is page

    def test_giant_fill_only_curve_dropped(self):
        # pdfminer surfaces a band drawn as a closed line path (rather than a
        # `re` rect operator) as a "curve" — same paint, same treatment.
        page = _FakePage([], curves=[_rect(540, 620, kind="curve")])
        out = _drop_background_rects(page)
        assert out is not page and out.curves == []


# ---------------------------------------------------------------------------
# Integration: a real PDF reproducing the band-swallow geometry.
# ---------------------------------------------------------------------------

_BAND = (36.0, 85.0, 576.0, 707.0)          # fill-only content band (~71% page)
_TABLE_Y = (100.0, 150.0, 200.0)            # 2-row ruled grid inside the band
_TABLE_X = (36.0, 306.0, 576.0)             # spans the band's full width, so
                                            # band verticals align with table
                                            # verticals and join the lattice
_PARA_Y = 400.0                             # paragraph inside the band, below
_PARA_TEXT = "This paragraph must survive extraction as body text."


@pytest.fixture()
def band_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    # Fill-only, stroke-less background band (the mno-b page-paint pattern).
    page.draw_rect(fitz.Rect(*_BAND), color=None, fill=(0.93, 0.93, 0.93))
    # Ruled 2x2 table sharing the band's x-extent.
    y0, y1, y2 = _TABLE_Y
    x0, x1, x2 = _TABLE_X
    for y in (y0, y1, y2):
        page.draw_line(fitz.Point(x0, y), fitz.Point(x2, y), color=(0, 0, 0))
    for x in (x0, x1, x2):
        page.draw_line(fitz.Point(x, y0), fitz.Point(x, y2), color=(0, 0, 0))
    for (tx, ty), cell in (((x0, y0), ("ColA", "ColB")), ((x0, y1), ("a1", "b1"))):
        page.insert_text(fitz.Point(tx + 6, ty + 20), cell[0], fontsize=10)
        page.insert_text(fitz.Point(x1 + 6, ty + 20), cell[1], fontsize=10)
    # Paragraph below the table, inside the band.
    page.insert_text(fitz.Point(40, _PARA_Y), _PARA_TEXT, fontsize=10)
    path = tmp_path / "band.pdf"
    doc.save(str(path))
    doc.close()
    return path


class TestBandPdfPlumberGeometry:
    """Pin the pdfplumber behavior the filter exists for. If either half ever
    fails after a pdfplumber upgrade, re-evaluate whether the filter is still
    needed (unfiltered half) or still sufficient (filtered half)."""

    def test_unfiltered_page_inflates_table_to_band(self, band_pdf):
        with pdfplumber.open(str(band_pdf)) as pdf:
            tables = pdf.pages[0].find_tables()
            assert tables, "fixture must produce a detected table"
            bbox = max(tables, key=lambda t: t.bbox[3] - t.bbox[1]).bbox
            # The band's bottom edge joined the lattice: bbox reaches (near)
            # the band bottom, far past the drawn grid.
            assert bbox[3] > _TABLE_Y[-1] + 100

    def test_filtered_page_bbox_hugs_the_grid(self, band_pdf):
        with pdfplumber.open(str(band_pdf)) as pdf:
            page = _drop_background_rects(pdf.pages[0])
            tables = page.find_tables()
            assert len(tables) == 1
            x0, top, x1, bottom = tables[0].bbox
            assert abs(top - _TABLE_Y[0]) < 5 and abs(bottom - _TABLE_Y[-1]) < 5
            assert abs(x0 - _TABLE_X[0]) < 5 and abs(x1 - _TABLE_X[-1]) < 5


class TestBandPdfEndToEnd:
    def test_paragraph_survives_and_table_is_tight(self, band_pdf):
        ir = PDFExtractor().extract(str(band_pdf))
        tables = ir.blocks_by_type(BlockType.TABLE)
        assert len(tables) == 1
        # The paragraph below the table is NOT swallowed into phantom rows.
        paragraphs = [b.text for b in ir.blocks_by_type(BlockType.PARAGRAPH)]
        assert any(_PARA_TEXT in t for t in paragraphs)
        assert not any(_PARA_TEXT in " ".join(c for c in row if c)
                       for row in tables[0].rows)
        # And the table itself still extracts its real cells.
        flat = " ".join(
            c for row in ([tables[0].headers] + tables[0].rows) for c in row if c
        )
        assert "a1" in flat and "b1" in flat
