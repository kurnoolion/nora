"""Unit tests for PDF strike-detection geometry helpers (FR-33 [D-031]).

These exercise `_table_is_struck` and `_span_struck` with synthetic
geometry — no PDF parsing required, no PyMuPDF dependency. The
end-to-end PDF strike behavior is exercised via the OA-corpus
integration test.
"""

from __future__ import annotations

from core.src.extraction.pdf_extractor import PDFExtractor


# ---------------------------------------------------------------------------
# _table_is_struck — multiple horizontal lines crossing the table bbox
# ---------------------------------------------------------------------------


def test_table_struck_when_multiple_lines_cross_full_width():
    """A table with two strike lines, each crossing the full width and
    falling within the vertical extent, is marked struck."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)  # width=200, height=100
    strike_lines = [
        # (y_center, x0, x1) — both cross full width within table y range
        (130.0, 50.0, 250.0),  # row 1 strike
        (170.0, 50.0, 250.0),  # row 2 strike
    ]
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is True


def test_table_not_struck_when_only_one_line_crosses():
    """A SINGLE strike line crossing the table is more often a divider
    or table-border artifact than a strike-through. Threshold is 2."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    strike_lines = [(150.0, 50.0, 250.0)]  # one line only
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is False


def test_table_not_struck_when_lines_outside_vertical_extent():
    """Strike lines above or below the table bbox don't count."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    strike_lines = [
        (50.0, 50.0, 250.0),   # above the table
        (250.0, 50.0, 250.0),  # below the table
    ]
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is False


def test_table_not_struck_when_horizontal_overlap_below_threshold():
    """Strike line covering only a small horizontal slice (e.g. a
    cell-divider hint) doesn't count toward the threshold."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    strike_lines = [
        (130.0, 50.0, 80.0),   # covers only 30/200 = 15% of width
        (170.0, 60.0, 100.0),  # covers only 40/200 = 20% of width
    ]
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is False


def test_table_struck_when_partial_but_above_threshold():
    """50%+ horizontal coverage suffices."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    strike_lines = [
        (130.0, 50.0, 160.0),  # 110/200 = 55%
        (170.0, 90.0, 250.0),  # 160/200 = 80%
    ]
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is True


def test_table_not_struck_with_empty_strike_list():
    """No strike lines → not struck."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    assert PDFExtractor._table_is_struck(table_bbox, []) is False


def test_table_not_struck_with_zero_width_bbox():
    """Degenerate table (zero width or height) is never struck — avoid
    division-by-zero / matching nothing."""
    assert PDFExtractor._table_is_struck((50.0, 100.0, 50.0, 200.0), [(150.0, 0.0, 100.0)]) is False
    assert PDFExtractor._table_is_struck((50.0, 100.0, 250.0, 100.0), [(100.0, 0.0, 300.0)]) is False


# ---------------------------------------------------------------------------
# _span_struck — single strike line crossing a span at its midline
# ---------------------------------------------------------------------------


def test_span_struck_when_line_crosses_midline():
    span_bbox = (50.0, 100.0, 200.0, 120.0)  # height 20, mid_y = 110
    strike_lines = [(110.0, 50.0, 200.0)]    # at midline, full width
    assert PDFExtractor._span_struck(span_bbox, strike_lines) is True


def test_span_not_struck_when_line_far_from_midline():
    span_bbox = (50.0, 100.0, 200.0, 120.0)
    strike_lines = [(150.0, 50.0, 200.0)]  # 30pt below midline — way outside ±40% of height
    assert PDFExtractor._span_struck(span_bbox, strike_lines) is False


def test_span_not_struck_when_horizontal_overlap_low():
    span_bbox = (50.0, 100.0, 200.0, 120.0)
    strike_lines = [(110.0, 50.0, 80.0)]  # only 30/150 = 20% overlap
    assert PDFExtractor._span_struck(span_bbox, strike_lines) is False


# ---------------------------------------------------------------------------
# min_lines parameterization (1-row tables can be struck with a single line)
# ---------------------------------------------------------------------------


def test_table_struck_with_single_line_when_min_lines_is_one():
    """A 1-row table can be marked struck on a single horizontal strike
    line — the caller passes `min_lines=1` for 1-row tables. Multi-row
    tables keep the default `min_lines=2` to avoid false positives
    from row-divider artifacts."""
    table_bbox = (50.0, 100.0, 250.0, 120.0)  # 1-row table, height=20
    strike_lines = [(110.0, 50.0, 250.0)]      # one strike line, full width
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines, min_lines=1) is True
    # Default min_lines=2 → not struck (need a second line)
    assert PDFExtractor._table_is_struck(table_bbox, strike_lines) is False

