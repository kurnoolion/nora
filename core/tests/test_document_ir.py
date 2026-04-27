"""Tests for DocumentIR serialize/deserialize round-trip."""

import json
from pathlib import Path

import pytest

from src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
)


def _make_text_block(page: int, index: int, text: str, size: float = 12.0) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=index, bbox=(72.0, 100.0, 540.0, 120.0)),
        text=text,
        font_info=FontInfo(size=size, bold=False, font_name="TestFont"),
    )


def _make_table_block(page: int, index: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=page, index=index, bbox=(72.0, 200.0, 540.0, 400.0)),
        headers=["Col A", "Col B"],
        rows=[["r1c1", "r1c2"], ["r2c1", "r2c2"]],
    )


def _make_image_block(page: int, index: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.IMAGE,
        position=Position(page=page, index=index, bbox=None),
        image_path="images/test.png",
        surrounding_text="Some context",
    )


def _make_ir() -> DocumentIR:
    return DocumentIR(
        source_file="TEST.pdf",
        source_format="pdf",
        mno="VZW",
        release="2026_feb",
        doc_type="requirement",
        content_blocks=[
            _make_text_block(1, 0, "Section heading", size=14.0),
            _make_text_block(1, 1, "Body paragraph with enough text to be meaningful."),
            _make_table_block(1, 2),
            _make_image_block(2, 3),
        ],
        extraction_metadata={"page_count": 2, "header_footer_patterns": ["Pattern #"]},
    )


class TestDocumentIRRoundTrip:
    def test_to_dict_returns_serializable(self):
        ir = _make_ir()
        d = ir.to_dict()
        # Should be JSON-serializable without errors
        json_str = json.dumps(d)
        assert len(json_str) > 0

    def test_round_trip_via_json(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)

        loaded = DocumentIR.load_json(json_path)

        assert loaded.source_file == original.source_file
        assert loaded.source_format == original.source_format
        assert loaded.mno == original.mno
        assert loaded.release == original.release
        assert loaded.doc_type == original.doc_type
        assert loaded.block_count == original.block_count

    def test_round_trip_preserves_text_blocks(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)

        text_blocks = loaded.blocks_by_type(BlockType.PARAGRAPH)
        assert len(text_blocks) == 2
        assert text_blocks[0].text == "Section heading"
        assert text_blocks[0].font_info is not None
        assert text_blocks[0].font_info.size == 14.0
        assert text_blocks[0].font_info.font_name == "TestFont"

    def test_round_trip_preserves_table_blocks(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)

        tables = loaded.blocks_by_type(BlockType.TABLE)
        assert len(tables) == 1
        assert tables[0].headers == ["Col A", "Col B"]
        assert tables[0].rows == [["r1c1", "r1c2"], ["r2c1", "r2c2"]]

    def test_round_trip_preserves_image_blocks(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)

        images = loaded.blocks_by_type(BlockType.IMAGE)
        assert len(images) == 1
        assert images[0].image_path == "images/test.png"
        assert images[0].surrounding_text == "Some context"
        assert images[0].position.bbox is None

    def test_round_trip_preserves_positions(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)

        for orig_b, loaded_b in zip(original.content_blocks, loaded.content_blocks):
            assert loaded_b.position.page == orig_b.position.page
            assert loaded_b.position.index == orig_b.position.index
            assert loaded_b.position.bbox == orig_b.position.bbox

    def test_round_trip_preserves_extraction_metadata(self, tmp_path: Path):
        original = _make_ir()
        json_path = tmp_path / "test_ir.json"
        original.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)

        assert loaded.extraction_metadata["page_count"] == 2
        assert loaded.extraction_metadata["header_footer_patterns"] == ["Pattern #"]

    def test_page_count_property(self):
        ir = _make_ir()
        assert ir.page_count == 2

    def test_page_count_empty(self):
        ir = DocumentIR(source_file="empty.pdf", source_format="pdf")
        assert ir.page_count == 0

    def test_blocks_by_type(self):
        ir = _make_ir()
        assert len(ir.blocks_by_type(BlockType.PARAGRAPH)) == 2
        assert len(ir.blocks_by_type(BlockType.TABLE)) == 1
        assert len(ir.blocks_by_type(BlockType.IMAGE)) == 1
        assert len(ir.blocks_by_type(BlockType.EMBEDDED_OBJECT)) == 0
