"""Guards for the opt-in text-strategy (borderless) table detection.

The pdfplumber integration itself needs a real PDF (validated on the corpus);
these unit-test the pure guard helpers that keep the text strategy from
over-detecting on prose. (mno-c-ingestion)
"""

from __future__ import annotations

from core.src.extraction.pdf_extractor import (
    _bbox_overlaps_any,
    _gutter_table_regions,
    _looks_tabular,
    _page_lines,
    _text_table_detection_enabled,
)


def _wd(x0: float, x1: float, top: float) -> dict:
    """A synthetic word occupying [x0,x1] on the line at `top`."""
    return {"text": "x", "x0": float(x0), "x1": float(x1),
            "top": float(top), "bottom": float(top) + 8.0}


def _regions(words: list[dict], **kw) -> list[tuple]:
    return _gutter_table_regions(_page_lines(words), **kw)


class TestFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("NORA_EXTRACT_TEXT_TABLES", raising=False)
        assert _text_table_detection_enabled() is False

    def test_on_when_truthy(self, monkeypatch):
        for v in ("1", "true", "YES", "on"):
            monkeypatch.setenv("NORA_EXTRACT_TEXT_TABLES", v)
            assert _text_table_detection_enabled() is True


class TestOverlapDedup:
    def test_overlapping_box_is_dropped(self):
        # Same box → 100% overlap → dedupe against the line-detected table.
        assert _bbox_overlaps_any((0, 0, 10, 10), [(0, 0, 10, 10)]) is True

    def test_disjoint_box_kept(self):
        assert _bbox_overlaps_any((0, 0, 10, 10), [(20, 20, 30, 30)]) is False

    def test_small_overlap_kept(self):
        # ~9% overlap (< 0.5 default) → not a duplicate.
        assert _bbox_overlaps_any((0, 0, 10, 10), [(7, 7, 30, 30)]) is False

    def test_no_others(self):
        assert _bbox_overlaps_any((0, 0, 10, 10), []) is False


class TestTabularShape:
    def test_two_by_two_kept(self):
        # The mno-c case: 2 columns, header + data rows.
        data = [["COL0", "COL1"], ["1", "text a"], ["2", "text b"]]
        assert _looks_tabular(data) is True

    def test_single_column_rejected(self):
        # Prose the text strategy mis-reads as a 1-column table.
        assert _looks_tabular([["a"], ["b"], ["c"]]) is False

    def test_single_row_rejected(self):
        assert _looks_tabular([["a", "b"]]) is False

    def test_empty_rejected(self):
        assert _looks_tabular([]) is False and _looks_tabular(None) is False


class TestGutterTableRegions:
    """Borderless-table region detection by persistent column gutters. Fixtures
    reproduce the structural cases observed on the real MNO-C corpus (validated
    separately against real page geometry); no corpus geometry is embedded."""

    def _multicol_table(self, rows: int = 5, top0: float = 100.0) -> list[dict]:
        # 3 columns with wrapped cells: every row has col1+col2, and col3 only on
        # some rows (continuation lines are single-column) — the wrapped-cell case.
        w: list[dict] = []
        for i in range(rows):
            y = top0 + i * 12.0
            w.append(_wd(36, 130, y))    # col1  (gutter 130..160)
            w.append(_wd(160, 250, y))   # col2  (gutter 250..400)
            if i % 2 == 0:
                w.append(_wd(400, 500, y))  # col3, present on alternate rows only
        return w

    def test_detects_multicolumn_table(self):
        regions = _regions(self._multicol_table())
        assert len(regions) == 1
        x0, top, x1, bottom = regions[0]
        assert x0 <= 36 and x1 >= 250 and top <= 100 and bottom >= 148

    def test_detects_two_column_table_with_offset_second_column(self):
        # The hard case: col2's short values sit on their OWN lines between col1's
        # wrapped lines, so no single line is multi-column — but the gutter
        # between them is empty across the whole run.
        w: list[dict] = []
        y = 600.0
        for a, b in [(36, 120), (36, 90), (36, 110), (36, 70), (36, 100)]:
            w.append(_wd(a, b, y))            # col1 line
            w.append(_wd(156, 172, y + 4))    # col2 value, its own line
            y += 12
        regions = _regions(w)
        assert len(regions) == 1

    def test_region_does_not_bleed_into_single_column_prose(self):
        # Table with a far-right column, then prose that never reaches that far.
        # The far-right gutter stays "empty" over the prose, so only the
        # two-sided-recent check stops the region bleeding downward.
        w = self._multicol_table(rows=5, top0=100.0)
        for y in range(180, 320, 14):
            w.append(_wd(36, 300, y))        # single-column prose, max x=300 < 400
        regions = _regions(w)
        assert len(regions) == 1
        assert regions[0][3] < 180          # bottom stays above the prose

    def test_two_tables_on_page_both_found(self):
        w = self._multicol_table(rows=4, top0=100.0)
        w += self._multicol_table(rows=4, top0=400.0)
        assert len(_regions(w)) == 2

    def test_prose_marker_gaps_not_detected(self):
        # List-ish prose: a ~5pt marker gap (< min_gutter) alternating with
        # full-width lines — no persistent >=8pt gutter.
        w: list[dict] = []
        for i, y in enumerate(range(100, 220, 12)):
            if i % 2 == 0:
                w.append(_wd(36, 48, y)); w.append(_wd(53, 210, y))  # 5pt gap
            else:
                w.append(_wd(36, 260, y))                             # full width
        assert _regions(w) == []

    def test_full_width_prose_not_detected(self):
        w = [_wd(36, 260 + (i % 5) * 20, 100 + i * 12) for i in range(12)]
        assert _regions(w) == []

    def test_narrow_gutter_below_threshold_rejected(self):
        # A 5pt interior gap is inter-column-ish but under min_gutter → not a
        # table (guards against splitting normally-spaced prose).
        w = [_wd(36, 120, 100 + i * 12) for i in range(4)]
        w += [_wd(125, 300, 100 + i * 12) for i in range(4)]  # gap 120..125 = 5pt
        assert _regions(w) == []


class TestPageLines:
    def test_merges_close_words_splits_at_gutter(self):
        # Two words 2pt apart merge; a 40pt gap stays split.
        lines = _page_lines([_wd(36, 60, 100), _wd(62, 90, 100), _wd(130, 200, 100)])
        assert len(lines) == 1
        assert lines[0]["segs"] == [(36.0, 90.0), (130.0, 200.0)]


class TestProfileFlag:
    def test_default_off_and_roundtrips(self):
        from core.src.profiler.profile_schema import DocumentProfile
        p = DocumentProfile(profile_name="t", profile_version=1,
                            created_from=[], last_updated="x")
        assert p.detect_text_tables is False           # default off (back-compat)
        p.detect_text_tables = True
        # survives to_dict → _from_dict (the load_json path)
        assert DocumentProfile._from_dict(p.to_dict()).detect_text_tables is True
