"""Phase-1 foundation for the Docling/LayoutProvider integration (mno-c-ingestion).

Covers the gate-independent pieces: the IR `html`/`caption` fields, the parser
preferring a table's HTML over the flat grid, the `layout_provider` profile flag,
and the core LayoutProvider contract. The DoclingProvider + extractor fusion are
built separately once the coordinate-alignment check passes.
"""

from __future__ import annotations

from core.src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
)
from core.src.parser.structural_parser import GenericStructuralParser
from core.src.profiler.profile_schema import (
    BodyText,
    CrossReferencePatterns,
    DocumentProfile,
    HeaderFooter,
    HeadingDetection,
    PlanMetadata,
    RequirementIdPattern,
)

_HTML = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"


def _profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="t", profile_version=1, created_from=[], last_updated="x",
        heading_detection=HeadingDetection(
            method="numbering", levels=[],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S", max_observed_depth=4),
        requirement_id=RequirementIdPattern(),
        plan_metadata=PlanMetadata(),
        document_zones=[], header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=4.0, font_size_max=5.5),
    )


def _heading(idx: int, text: str) -> ContentBlock:
    return ContentBlock(type=BlockType.PARAGRAPH, position=Position(page=1, index=idx),
                        text=text, font_info=FontInfo(size=9.8, bold=True))


def _parse(blocks: list[ContentBlock]):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="M", release="R",
                    content_blocks=blocks)
    return GenericStructuralParser(_profile()).parse(ir)


class TestParserPrefersTableHtml:
    def test_html_inlined_and_stored_on_requirement(self):
        tree = _parse([
            _heading(0, "4 Requirements"),
            ContentBlock(type=BlockType.TABLE, position=Position(page=1, index=1),
                         html=_HTML),
        ])
        sec = next(r for r in tree.requirements if r.section_number == "4")
        assert sec.tables and sec.tables[0].html == _HTML   # structured metadata
        assert _HTML in sec.text                             # inlined at position

    def test_falls_back_to_markdown_without_html(self):
        # Regression: the geometric path (no html) still renders headers/rows.
        tree = _parse([
            _heading(0, "4 Requirements"),
            ContentBlock(type=BlockType.TABLE, position=Position(page=1, index=1),
                         headers=["A", "B"], rows=[["1", "2"]]),
        ])
        sec = next(r for r in tree.requirements if r.section_number == "4")
        assert not sec.tables[0].html
        assert "| A | B |" in sec.text and "<table>" not in sec.text


class TestProviderTableGridTrim:
    """A provider table carries lossless HTML; the redundant flat headers/rows are
    kept only when the table-anchored req-id path needs them."""

    def _blocks(self):
        return [
            _heading(0, "4 Requirements"),
            ContentBlock(type=BlockType.TABLE, position=Position(page=1, index=1),
                         html=_HTML, headers=["A", "B"], rows=[["1", "2"]]),
        ]

    def _parse_anchoring(self, blocks, anchoring: bool):
        prof = _profile()
        prof.enable_table_anchored_extraction = anchoring
        for i, b in enumerate(blocks):
            b.position.index = i
        ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="M",
                        release="R", content_blocks=blocks)
        return GenericStructuralParser(prof).parse(ir)

    def test_grid_dropped_when_anchoring_off(self):
        tree = self._parse_anchoring(self._blocks(), anchoring=False)
        td = next(r for r in tree.requirements if r.section_number == "4").tables[0]
        assert td.html == _HTML
        assert td.headers == [] and td.rows == []        # redundant grid dropped
        assert _HTML in next(r for r in tree.requirements
                             if r.section_number == "4").text  # still inlined

    def test_grid_kept_when_anchoring_on(self):
        tree = self._parse_anchoring(self._blocks(), anchoring=True)
        td = next(r for r in tree.requirements if r.section_number == "4").tables[0]
        assert td.html == _HTML
        assert td.headers == ["A", "B"] and td.rows == [["1", "2"]]  # kept for anchoring

    def test_geometric_table_grid_always_kept(self):
        # No html (geometric path) → headers/rows preserved regardless of anchoring.
        blocks = [
            _heading(0, "4 Requirements"),
            ContentBlock(type=BlockType.TABLE, position=Position(page=1, index=1),
                         headers=["A", "B"], rows=[["1", "2"]]),
        ]
        td = next(r for r in self._parse_anchoring(blocks, anchoring=False).requirements
                  if r.section_number == "4").tables[0]
        assert td.headers == ["A", "B"] and td.rows == [["1", "2"]] and not td.html


class TestIRRoundTrip:
    def test_html_and_caption_survive_save_load(self, tmp_path):
        ir = DocumentIR(source_file="f.pdf", source_format="pdf", content_blocks=[
            ContentBlock(type=BlockType.TABLE, position=Position(page=1, index=0),
                         html=_HTML),
            ContentBlock(type=BlockType.IMAGE, position=Position(page=1, index=1),
                         image_path="p1_000.png", caption="Figure 1: call flow"),
        ])
        p = tmp_path / "ir.json"
        ir.save_json(p)
        out = DocumentIR.load_json(p)
        assert out.content_blocks[0].html == _HTML
        assert out.content_blocks[1].caption == "Figure 1: call flow"


class TestProfileFlag:
    def test_layout_provider_default_and_roundtrip(self):
        p = DocumentProfile(profile_name="t", profile_version=1,
                            created_from=[], last_updated="x")
        assert p.layout_provider == ""                        # default: geometric path
        p.layout_provider = "docling"
        assert DocumentProfile._from_dict(p.to_dict()).layout_provider == "docling"


class TestProtocol:
    def test_contract_importable_and_constructs(self):
        from core.src.extraction.layout_provider import (
            LayoutFigure, LayoutProvider, LayoutStructures, LayoutTable,
        )
        s = LayoutStructures(
            tables=[LayoutTable(page=1, bbox=(0, 0, 10, 10), html=_HTML)],
            figures=[LayoutFigure(page=2, image_path="f.png", caption="c")],
            page_sizes={1: (612.0, 792.0)})
        assert s.tables[0].html == _HTML and s.figures[0].page == 2
        assert hasattr(LayoutProvider, "extract_layout")
