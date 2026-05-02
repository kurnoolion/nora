"""Tests for table-cell req-ID anchoring in GenericStructuralParser.

Hand-crafted in-memory DocumentIR + DocumentProfile fixtures — no PDF
extraction, no real-doc dependency. Exercises the table-anchored
Requirement detection path added to _build_sections.
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
    HeadingLevel,
    PlanMetadata,
    RequirementIdPattern,
)


# ---------------------------------------------------------------------------
# Fixture helpers — minimum profile + IR shaped like OA
# ---------------------------------------------------------------------------


def _profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="test",
        profile_version=1,
        created_from=[],
        last_updated="2026-04-28",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[
                HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True),
            ],
            numbering_pattern=r"^\d+(\.\d+)*\s",
            max_observed_depth=3,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"VZ_REQ_[A-Z0-9_]+_\d+",
            components={"separator": "_", "plan_id_position": 2, "number_position": 3},
            sample_ids=[],
            total_found=0,
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _heading_block(idx: int, page: int, text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.HEADING,  # NB: profile path uses paragraph-with-heading-font for OA;
                                  # but ContentBlock.type=HEADING is a distinct case the parser
                                  # falls through. We rely on _classify_heading via paragraph
                                  # below — see _para_heading_block.
        position=Position(page=page, index=idx),
        text=text,
    )


def _para_heading_block(idx: int, page: int, text: str) -> ContentBlock:
    """Paragraph block with heading-sized bold font — what _classify_heading expects."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
    )


def _body_block(idx: int, page: int, text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=12.0, bold=False),
    )


def _small_id_block(idx: int, page: int, text: str) -> ContentBlock:
    """Paragraph in small font carrying just a req-ID — paragraph anchor path."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=7.0, bold=False),
    )


def _table_block(
    idx: int, page: int, headers: list[str], rows: list[list[str]]
) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=page, index=idx),
        headers=headers,
        rows=rows,
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
    # Re-index sequentially for a clean reading-order.
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(
        source_file="fixture.pdf",
        source_format="pdf",
        mno="VZW",
        release="OA-test",
        doc_type="requirement",
        content_blocks=blocks,
    )


def _parse(blocks: list[ContentBlock]):
    return GenericStructuralParser(_profile()).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestColumnOneAnchoring:
    def test_col1_ids_become_requirements(self):
        blocks = [
            _para_heading_block(0, 1, "1.6 Some Section"),
            _body_block(1, 1, "Intro text."),
            _table_block(
                2, 1,
                headers=["Req ID", "Description"],
                rows=[
                    ["VZ_REQ_LTEB13NAC_36963", "supports IPv6"],
                    ["VZ_REQ_LTEB13NAC_36964", "MTU = 1500"],
                    ["VZ_REQ_LTEB13NAC_36965", "DNS via DHCPv6"],
                ],
            ),
        ]
        tree = _parse(blocks)
        ids = {r.req_id for r in tree.requirements if r.req_id}
        assert {"VZ_REQ_LTEB13NAC_36963", "VZ_REQ_LTEB13NAC_36964", "VZ_REQ_LTEB13NAC_36965"} <= ids

    def test_table_anchored_inherit_parent_section(self):
        blocks = [
            _para_heading_block(0, 1, "1.6 Parent Section"),
            _table_block(
                1, 1,
                headers=["Req ID", "Description"],
                rows=[["VZ_REQ_LTEB13NAC_36963", "supports IPv6"]],
            ),
        ]
        tree = _parse(blocks)
        anchored = next(r for r in tree.requirements if r.req_id == "VZ_REQ_LTEB13NAC_36963")
        assert anchored.parent_section == "1.6"
        assert anchored.section_number == ""  # no own section
        # Body content preserves the column→value mapping
        assert "Description: supports IPv6" in anchored.text
        assert "Req ID: VZ_REQ_LTEB13NAC_36963" in anchored.text


class TestDisableTableAnchoredExtraction:
    """When `profile.enable_table_anchored_extraction` is False, table cells
    never produce Requirement nodes — appropriate for paragraph-only-
    requirement corpora (Verizon OA) where table-cell req_ids are
    always cross-references, changelog entries, or other non-requirement
    content."""

    def test_table_only_id_dropped_when_disabled(self):
        profile = _profile()
        profile.enable_table_anchored_extraction = False
        blocks = [
            _para_heading_block(0, 1, "1.6 Some Section"),
            _small_id_block(1, 1, "VZ_REQ_LTEB13NAC_36963"),  # paragraph anchor
            _body_block(2, 1, "actual requirement body text"),
            _para_heading_block(3, 5, "9.1 Cross-Reference Tables"),
            _table_block(
                4, 5,
                headers=["Req ID"],
                rows=[
                    ["VZ_REQ_LTEB13NAC_99999"],   # only here — would be table-anchored
                    ["VZ_REQ_LTEB13NAC_36963"],   # also paragraph-anchored
                ],
            ),
        ]
        tree = GenericStructuralParser(profile).parse(_doc(blocks))
        ids = {r.req_id for r in tree.requirements if r.req_id}
        # _36963 still present (paragraph anchor wins regardless of flag)
        assert "VZ_REQ_LTEB13NAC_36963" in ids
        # _99999 (only-in-table) NOT present — table extraction disabled
        assert "VZ_REQ_LTEB13NAC_99999" not in ids

    def test_table_only_id_kept_when_enabled(self):
        """Default-True path: only-in-table id surfaces as a table-anchored
        Requirement (D-027 behavior preserved)."""
        profile = _profile()
        # Default is True — verify explicitly.
        assert profile.enable_table_anchored_extraction is True
        blocks = [
            _para_heading_block(0, 1, "1.6 Some Section"),
            _table_block(
                1, 1,
                headers=["Req ID"],
                rows=[["VZ_REQ_LTEB13NAC_99999"]],  # only in table
            ),
        ]
        tree = GenericStructuralParser(profile).parse(_doc(blocks))
        ids = {r.req_id for r in tree.requirements if r.req_id}
        assert "VZ_REQ_LTEB13NAC_99999" in ids


class TestParagraphWinsOnDuplicate:
    def test_paragraph_anchor_first_then_table_with_same_id(self):
        blocks = [
            _para_heading_block(0, 1, "1.6 Some Section"),
            _small_id_block(1, 1, "VZ_REQ_LTEB13NAC_36963"),  # paragraph anchor wins
            _body_block(2, 1, "actual requirement body text"),
            _para_heading_block(3, 5, "9.1 Cross-Reference Tables"),
            _table_block(
                4, 5,
                headers=["Req ID", "Page"],
                rows=[["VZ_REQ_LTEB13NAC_36963", "1"]],  # same id — must dedup
            ),
        ]
        tree = _parse(blocks)
        instances = [r for r in tree.requirements if r.req_id == "VZ_REQ_LTEB13NAC_36963"]
        assert len(instances) == 1
        # The surviving instance is the paragraph anchor — section 1.6, has body text
        assert instances[0].section_number == "1.6"
        assert "actual requirement body text" in instances[0].text

    def test_table_anchor_first_then_paragraph_anchor_same_id(self):
        # Reverse order — table appears FIRST (e.g. cross-reference table on
        # an early page), then the paragraph anchor for the same req_id
        # arrives later. Paragraph still wins: the parser defers
        # table-anchored extraction to a second pass after paragraph_req_ids
        # is fully populated, so the duplicate table-anchored Requirement
        # is never created. Only the paragraph-anchored node survives.
        blocks = [
            _para_heading_block(0, 1, "1.6 Listing Section"),
            _table_block(
                1, 1,
                headers=["Req ID"],
                rows=[["VZ_REQ_LTEB13NAC_36963"]],
            ),
            _para_heading_block(2, 5, "9.1 Detail Section"),
            _small_id_block(3, 5, "VZ_REQ_LTEB13NAC_36963"),
            _body_block(4, 5, "detail body"),
        ]
        tree = _parse(blocks)
        nodes = [r for r in tree.requirements if r.req_id == "VZ_REQ_LTEB13NAC_36963"]
        assert len(nodes) == 1, (
            f"paragraph wins regardless of ordering; got {len(nodes)} nodes"
        )
        assert nodes[0].section_number == "9.1"
        assert "detail body" in nodes[0].text


class TestEdgeCases:
    def test_header_row_label_does_not_anchor(self):
        # The header literal "Requirement ID" doesn't match the regex —
        # only actual VZ_REQ_* tokens do — so header text never anchors.
        blocks = [
            _para_heading_block(0, 1, "1.6 Section"),
            _table_block(
                1, 1,
                headers=["Requirement ID", "Description"],
                rows=[["not a req", "still not a req"]],
            ),
        ]
        tree = _parse(blocks)
        assert all(r.section_number for r in tree.requirements), \
            "no Requirement should be table-anchored when no cell matches the regex"

    def test_table_with_no_preceding_section_is_skipped(self):
        # No paragraph heading before the table → no current_section →
        # table-anchored detection has no parent → silently skip.
        blocks = [
            _table_block(
                0, 1,
                headers=["Req ID", "Description"],
                rows=[["VZ_REQ_LTEAT_99999", "should not anchor"]],
            ),
        ]
        tree = _parse(blocks)
        assert tree.requirements == [], (
            "tables before any heading must not produce requirements"
        )

    def test_dedup_within_single_table(self):
        # Same req_id repeated across rows of one table → one Requirement.
        blocks = [
            _para_heading_block(0, 1, "1.6 Section"),
            _table_block(
                1, 1,
                headers=["Req ID", "Note"],
                rows=[
                    ["VZ_REQ_LTEAT_42", "first mention"],
                    ["VZ_REQ_LTEAT_42", "duplicate mention"],
                ],
            ),
        ]
        tree = _parse(blocks)
        instances = [r for r in tree.requirements if r.req_id == "VZ_REQ_LTEAT_42"]
        assert len(instances) == 1
        # First row wins → its description is preserved
        assert "first mention" in instances[0].text

    def test_fallback_to_other_cells_when_col1_empty(self):
        # OA tables sometimes put the req-ID in column 2 with column 1 used
        # for a row label like "Req-1". Fallback path should still anchor.
        blocks = [
            _para_heading_block(0, 1, "1.6 Section"),
            _table_block(
                1, 1,
                headers=["Label", "Req ID", "Description"],
                rows=[["Req-1", "VZ_REQ_LTEAT_77", "value"]],
            ),
        ]
        tree = _parse(blocks)
        ids = {r.req_id for r in tree.requirements if r.req_id}
        assert "VZ_REQ_LTEAT_77" in ids


class TestParentChildLinkage:
    def test_table_anchored_appears_in_parent_children(self):
        blocks = [
            _para_heading_block(0, 1, "1.6 Parent"),
            _small_id_block(1, 1, "VZ_REQ_LTEAT_100"),  # parent has paragraph anchor
            _body_block(2, 1, "parent body"),
            _table_block(
                3, 1,
                headers=["Req ID"],
                rows=[["VZ_REQ_LTEAT_200"]],
            ),
        ]
        tree = _parse(blocks)
        parent = next(r for r in tree.requirements if r.req_id == "VZ_REQ_LTEAT_100")
        assert "VZ_REQ_LTEAT_200" in parent.children

    def test_table_anchored_inherits_zone_type(self):
        # Build a profile with a zone for section 1.* → "hardware_specs".
        from core.src.profiler.profile_schema import DocumentZone
        prof = _profile()
        prof.document_zones = [
            DocumentZone(
                section_pattern=r"^1\.",
                zone_type="hardware_specs",
                description="HW",
                heading_text="Hardware",
            )
        ]
        blocks = [
            _para_heading_block(0, 1, "1.6 Hardware Section"),
            _table_block(
                1, 1,
                headers=["Req ID"],
                rows=[["VZ_REQ_LTEAT_50"]],
            ),
        ]
        tree = GenericStructuralParser(prof).parse(_doc(blocks))
        anchored = next(r for r in tree.requirements if r.req_id == "VZ_REQ_LTEAT_50")
        assert anchored.zone_type == "hardware_specs"
