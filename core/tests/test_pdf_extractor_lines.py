"""Tests for PDF source-line-boundary preservation (ContentBlock.lines).

The PDF extractor groups several source lines into one paragraph block and
``text`` joins them with spaces; ``lines`` preserves the original per-line
split so the parser can keep a heading/title line distinct from the body line
beneath it. Invariant: ``" ".join(block.lines) == block.text``.

These unit-test the pure helpers (`_extract_text_segments`, `_make_group`)
with synthetic pymupdf-shaped block dicts — no real PDF needed.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from core.src.extraction.pdf_extractor import PDFExtractor


def _span(text, size=11.0, color=0, flags=0, font="Arial"):
    return {
        "text": text, "size": size, "color": color, "flags": flags,
        "font": font, "bbox": (0.0, 0.0, 10.0, 10.0),
    }


def _fitz_block(lines: list[list[str]]) -> dict:
    """A pymupdf-shaped block dict: lines -> spans (one span per word here)."""
    return {"lines": [{"spans": [_span(w) for w in words]} for words in lines]}


class TestExtractTextSegmentsLineIndex:
    def test_segments_carry_source_line_index(self):
        ext = PDFExtractor()
        block = _fitz_block([["5.1.2", "Idle", "Mode"], ["The", "device", "shall"]])
        segs = ext._extract_text_segments(block)
        # line 0 = the heading words, line 1 = the body words
        assert [s["line"] for s in segs] == [0, 0, 0, 1, 1, 1]


class TestMakeGroupLines:
    def test_lines_reconstructed_and_join_equals_text(self):
        ext = PDFExtractor()
        block = _fitz_block([["5.1.2", "Idle", "Mode"], ["The", "device", "shall"]])
        segs = ext._extract_text_segments(block)
        group = PDFExtractor._make_group(segs)
        # heading line and body line preserved separately ...
        assert group["lines"] == ["5.1.2 Idle Mode", "The device shall"]
        # ... and the joined form is byte-identical to text (no regression).
        assert " ".join(group["lines"]) == group["text"]

    def test_requirement_header_separated_from_body(self):
        ext = PDFExtractor()
        block = _fitz_block([
            ["ABC-FOO-12345", "Re-registration", "timer"],   # req_id + title line
            ["The", "device", "shall", "re-register."],       # body line
        ])
        group = PDFExtractor._make_group(ext._extract_text_segments(block))
        assert group["lines"][0] == "ABC-FOO-12345 Re-registration timer"
        assert group["lines"][1] == "The device shall re-register."

    def test_single_line_block_one_entry(self):
        ext = PDFExtractor()
        group = PDFExtractor._make_group(
            ext._extract_text_segments(_fitz_block([["Just", "one", "line"]]))
        )
        assert group["lines"] == ["Just one line"]
        assert " ".join(group["lines"]) == group["text"]

    def test_segments_without_line_key_collapse_to_one_line(self):
        # Legacy/synthetic segments lacking a "line" key must not crash and
        # collapse to a single line (back-compat with old callers).
        segs = [
            {"text": "a", "size": 11.0, "color": 0, "bold": False,
             "italic": False, "font": "Arial", "strikethrough": False, "len": 1},
            {"text": "b", "size": 11.0, "color": 0, "bold": False,
             "italic": False, "font": "Arial", "strikethrough": False, "len": 1},
        ]
        group = PDFExtractor._make_group(segs)
        assert group["lines"] == ["a b"]
        assert " ".join(group["lines"]) == group["text"]


class TestRealPdfRoundTrip:
    """End-to-end through real pymupdf — fitz groups a heading/title line and
    the body line beneath it into ONE block (the MNO-B merge); `lines` must
    recover the boundary while `text` stays the flattened run-on."""

    def test_two_lines_in_one_block_preserved(self):
        fitz = pytest.importorskip("fitz")
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 520, 200),
            "ABC-FOO-12345 Re-registration timer\n"
            "The device shall re-register within 30 seconds of attach.",
            fontsize=11, fontname="helv",
        )
        path = os.path.join(tempfile.mkdtemp(), "probe.pdf")
        doc.save(path)
        doc.close()

        ir = PDFExtractor().extract(path)
        paras = [b for b in ir.content_blocks if b.type.name == "PARAGRAPH" and b.text]
        # the merged block carries both source lines, split correctly ...
        merged = next(b for b in paras if "Re-registration timer" in b.text)
        assert merged.lines == [
            "ABC-FOO-12345 Re-registration timer",
            "The device shall re-register within 30 seconds of attach.",
        ]
        # ... and text is the unchanged run-on (invariant holds end-to-end)
        assert " ".join(merged.lines) == merged.text
