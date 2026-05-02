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


# ---------------------------------------------------------------------------
# row-edge filter (drops grid lines that look like strike marks)
# ---------------------------------------------------------------------------


def test_table_not_struck_when_lines_align_with_row_edges():
    """Real-world false positive: a 3-row table whose grid lines look
    like strike-throughs by geometry alone. Without `row_edge_ys`, the
    heuristic flags the table struck. With the row-edge filter, the
    grid lines are correctly excluded and the table is preserved."""
    # Table from 100 to 200 with rows ending at 130, 160, 200 (3 rows).
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    row_edges = [100.0, 130.0, 160.0, 200.0]  # top + 2 dividers + bottom
    # Grid lines at every row edge — three of them (top, divider, divider) are
    # within table y-extent and span the full width.
    grid_lines = [
        (100.0, 50.0, 250.0),
        (130.0, 50.0, 250.0),
        (160.0, 50.0, 250.0),
        (200.0, 50.0, 250.0),
    ]
    # WITHOUT filter — falsely struck (4 lines >> min_lines=2).
    assert PDFExtractor._table_is_struck(table_bbox, grid_lines) is True
    # WITH filter — all 4 lines aligned with row edges → not struck.
    assert PDFExtractor._table_is_struck(
        table_bbox, grid_lines, row_edge_ys=row_edges
    ) is False


def test_table_struck_when_lines_in_row_middles_not_at_edges():
    """Mid-row strike lines (genuine strike-throughs of cell text) are
    NOT filtered — the table is correctly marked struck."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    row_edges = [100.0, 130.0, 160.0, 200.0]  # 3 rows
    # Strike lines at the MIDDLE of two rows (y=115, y=145), well away
    # from row edges.
    mid_row_lines = [
        (115.0, 50.0, 250.0),
        (145.0, 50.0, 250.0),
    ]
    assert PDFExtractor._table_is_struck(
        table_bbox, mid_row_lines, row_edge_ys=row_edges
    ) is True


def test_table_edge_filter_handles_paired_edges_within_tolerance():
    """PDF generators sometimes emit two horizontal lines at almost-
    identical y values for the same row boundary (e.g. top-of-row-i
    and bottom-of-row-(i-1) drawn separately at y=615.73 and y=616.48).
    Both must be filtered when either matches a known edge within the
    `edge_tol=1.5` window."""
    table_bbox = (50.0, 100.0, 250.0, 720.0)
    row_edges = [616.1]  # nominal edge
    paired = [
        (615.73, 50.0, 250.0),  # 0.37pt below
        (616.48, 50.0, 250.0),  # 0.38pt above
    ]
    assert PDFExtractor._table_is_struck(
        table_bbox, paired, row_edge_ys=row_edges
    ) is False


def test_table_edge_filter_with_no_row_edges_supplied():
    """Backwards compatibility — when caller passes no row_edge_ys
    (legacy path / pdfplumber row.bbox unavailable), behavior matches
    the pre-filter logic."""
    table_bbox = (50.0, 100.0, 250.0, 200.0)
    grid_lines = [
        (130.0, 50.0, 250.0),
        (160.0, 50.0, 250.0),
    ]
    # No filter → 2 lines hit threshold → struck (legacy behavior).
    assert PDFExtractor._table_is_struck(table_bbox, grid_lines) is True
    assert PDFExtractor._table_is_struck(
        table_bbox, grid_lines, row_edge_ys=[]
    ) is True
    assert PDFExtractor._table_is_struck(
        table_bbox, grid_lines, row_edge_ys=None
    ) is True


# ---------------------------------------------------------------------------
# _detect_struck_rows — per-row strike detection
# ---------------------------------------------------------------------------


class _FakeRow:
    """Minimal stand-in for `pdfplumber.table.Row`. The real class
    exposes a `.bbox` attribute as `(x0, top, x1, bottom)`; that's all
    `_detect_struck_rows` reads."""
    def __init__(self, bbox: tuple[float, float, float, float]):
        self.bbox = bbox


class _FakeTable:
    """Minimal stand-in for `pdfplumber.table.Table` exposing only
    `.rows` — the interface `_detect_struck_rows` consumes."""
    def __init__(self, rows: list[_FakeRow]):
        self.rows = rows


def _t(rows_yranges: list[tuple[float, float]]) -> _FakeTable:
    """Convenience: build a fake table from a list of (y_top, y_bot)
    row extents. x-range is fixed (irrelevant for row-strike detection)."""
    return _FakeTable([_FakeRow((50.0, t, 250.0, b)) for t, b in rows_yranges])


def test_detect_struck_rows_marks_data_rows_with_interior_strike():
    """A 4-row table (1 header + 3 data) where rows 1 and 3 (indices
    1, 3 in pdfplumber) have strike lines mid-row. `_detect_struck_rows`
    returns DATA-row indices (0-based, header excluded), so it should
    flag indices [0, 2]."""
    table = _t([(100.0, 130.0), (130.0, 160.0), (160.0, 190.0), (190.0, 220.0)])
    strike_lines = [
        # Row 1 interior (y=130-160, mid-row at ~145)
        (145.0, 70.0, 200.0),
        # Row 3 interior (y=190-220, mid-row at ~205)
        (205.0, 70.0, 200.0),
    ]
    result = PDFExtractor._detect_struck_rows(table, strike_lines)
    # Header is row 0 of pdfplumber → excluded; data row 0 ↔ pdfplumber row 1.
    assert sorted(result) == [0, 2]


def test_detect_struck_rows_excludes_lines_at_row_edges():
    """Strike candidates aligned with row top/bottom (within edge_tol)
    are row-grid lines, not strike-throughs. They must be excluded
    from row-strike detection by the strict-interior `<` checks."""
    table = _t([(100.0, 130.0), (130.0, 160.0), (160.0, 190.0)])
    grid_lines = [
        (130.0, 50.0, 250.0),  # divider between rows 0 and 1
        (160.0, 50.0, 250.0),  # divider between rows 1 and 2
    ]
    result = PDFExtractor._detect_struck_rows(table, grid_lines)
    assert result == []


def test_detect_struck_rows_returns_empty_for_header_only():
    """A 1-row "table" (just a header) has no data rows; detection is
    a no-op even with strike lines on the page."""
    table = _t([(100.0, 130.0)])
    strike_lines = [(115.0, 50.0, 250.0)]  # mid-row of the header
    assert PDFExtractor._detect_struck_rows(table, strike_lines) == []


def test_detect_struck_rows_counts_one_line_per_row_as_struck():
    """Even a single short strike line in row interior (length already
    ≥5pt by the page-level collector) is enough — real strikes draw
    multiple short segments per word but cell-level detection should
    not require multi-segment confirmation."""
    table = _t([(100.0, 130.0), (130.0, 160.0)])
    strike_lines = [(145.0, 70.0, 95.0)]  # one short line in row 1 interior
    result = PDFExtractor._detect_struck_rows(table, strike_lines)
    assert result == [0]  # data row 0 (pdfplumber row 1)


