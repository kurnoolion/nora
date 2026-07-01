"""Guards for the opt-in text-strategy (borderless) table detection.

The pdfplumber integration itself needs a real PDF (validated on the corpus);
these unit-test the pure guard helpers that keep the text strategy from
over-detecting on prose. (mno-c-ingestion)
"""

from __future__ import annotations

from core.src.extraction.pdf_extractor import (
    _bbox_overlaps_any,
    _looks_tabular,
    _text_table_detection_enabled,
)


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
