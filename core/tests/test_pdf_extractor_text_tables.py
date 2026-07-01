"""Guards for the opt-in text-strategy (borderless) table detection.

The pdfplumber integration itself needs a real PDF (validated on the corpus);
these unit-test the pure guard helpers that keep the text strategy from
over-detecting on prose. (mno-c-ingestion)
"""

from __future__ import annotations

from core.src.extraction.pdf_extractor import (
    _bbox_overlaps_any,
    _looks_tabular,
    _numbered_row_regions,
    _text_table_detection_enabled,
)


def _word(text: str, x0: float, top: float, *, w: float = 10.0, h: float = 8.0) -> dict:
    return {"text": text, "x0": x0, "x1": x0 + w, "top": top, "bottom": top + h}


def _numbered_table_words(n_rows: int, *, x0: float = 50.0, start: int = 1) -> list[dict]:
    """A borderless numbered-row table: a header line plus `n_rows` rows, each
    starting with a sequential integer in a shared left column."""
    words = [_word("Item", x0, 100.0), _word("Description", x0 + 40, 100.0)]
    for i in range(n_rows):
        top = 112.0 + i * 12.0
        words.append(_word(str(start + i), x0, top, w=6.0))          # row marker
        words.append(_word(f"cellA{i}", x0 + 40, top))               # data column
        words.append(_word(f"cellB{i}", x0 + 90, top))
    return words


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


class TestNumberedRowRegions:
    def test_detects_three_row_table(self):
        regions = _numbered_row_regions(_numbered_table_words(3))
        assert len(regions) == 1
        x0, top, x1, bottom = regions[0]
        # Region extends above the first marker (top=112) to include the header
        # line (top=100), and below the last marker.
        assert top <= 100.0 + 0.1
        assert bottom >= 136.0
        assert x0 <= 50.0 and x1 >= 90.0

    def test_rejects_two_row_run_below_min(self):
        # Only 2 numbered rows → below min_rows=3, not a table.
        assert _numbered_row_regions(_numbered_table_words(2)) == []

    def test_rejects_non_consecutive_integers(self):
        words = [_word("Item", 50, 100)]
        for val, top in [(1, 112), (5, 124), (9, 136)]:  # not +1 increments
            words.append(_word(str(val), 50, top, w=6))
            words.append(_word("data", 90, top))
        assert _numbered_row_regions(words) == []

    def test_rejects_misaligned_columns(self):
        # Sequential integers but each at a different x0 → not a left column.
        words = [_word("Item", 50, 100)]
        for i, x0 in enumerate((50, 120, 200)):
            words.append(_word(str(i + 1), x0, 112 + i * 12, w=6))
            words.append(_word("data", x0 + 30, 112 + i * 12))
        assert _numbered_row_regions(words) == []

    def test_numbered_list_periods_not_detected(self):
        # "1." / "2." keep the period → isdigit() False → not markers.
        words = [_word("Intro", 50, 100)]
        for i in range(4):
            words.append(_word(f"{i + 1}.", 50, 112 + i * 12, w=8))
            words.append(_word("some prose text here", 70, 112 + i * 12, w=120))
        assert _numbered_row_regions(words) == []

    def test_two_independent_tables_both_found(self):
        top_table = _numbered_table_words(3, x0=50, start=1)
        # Second table lower on the page, offset in y and restarting at 1.
        bottom = [
            {"text": w["text"], "x0": w["x0"], "x1": w["x1"],
             "top": w["top"] + 300.0, "bottom": w["bottom"] + 300.0}
            for w in _numbered_table_words(3, x0=50, start=1)
        ]
        regions = _numbered_row_regions(top_table + bottom)
        assert len(regions) == 2


class TestProfileFlag:
    def test_default_off_and_roundtrips(self):
        from core.src.profiler.profile_schema import DocumentProfile
        p = DocumentProfile(profile_name="t", profile_version=1,
                            created_from=[], last_updated="x")
        assert p.detect_text_tables is False           # default off (back-compat)
        p.detect_text_tables = True
        # survives to_dict → _from_dict (the load_json path)
        assert DocumentProfile._from_dict(p.to_dict()).detect_text_tables is True
