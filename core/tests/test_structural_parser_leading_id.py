"""Tests for leading-id body-block requirement detection (D-DRAFT-2).

Hand-crafted in-memory DocumentIR + DocumentProfile fixtures — no PDF
extraction, no real-doc dependency. Exercises
``requirement_id.detection_mode == "leading_id_body"``: a corpus whose
sections/subsections carry no req_ids and whose requirements are flat body
paragraphs that BEGIN with the req_id (``<PREFIX>-<PLAN>-<DIGITS>``).

Generic example ids only (``ABC-FOO-001``) — no real corpus plan codes.
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
# Fixture helpers
# ---------------------------------------------------------------------------


def _profile(detection_mode: str = "leading_id_body") -> DocumentProfile:
    return DocumentProfile(
        profile_name="test-leading-id",
        profile_version=1,
        created_from=[],
        last_updated="2026-06-14",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[
                HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True),
            ],
            numbering_pattern=r"^\d+(\.\d+)*\s",
            max_observed_depth=3,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"ABC-[A-Z]+-\d+",
            components={"separator": "-", "plan_id_position": 1, "number_position": 2},
            anchor="leading_text",
            detection_mode=detection_mode,
            sample_ids=[],
            total_found=0,
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _heading(idx: int, text: str) -> ContentBlock:
    """Paragraph block with heading-sized bold font — a non-requirement
    section heading in the leading-id model."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
    )


def _body(idx: int, text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=12.0, bold=False),
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(
        source_file="fixture.pdf",
        source_format="pdf",
        mno="MNOB",
        release="Jun2026",
        doc_type="requirement",
        content_blocks=blocks,
    )


# A document shaped like MNO-B: headings have no req_ids; requirements are
# flat body paragraphs leading with their id; one requirement's text spills
# over to a continuation block; one requirement mentions another inline.
def _mnob_blocks() -> list[ContentBlock]:
    return [
        _heading(0, "1 General"),
        _heading(1, "1.1 Device Requirements"),
        _body(2, "This subsection covers device behavior."),       # preamble
        _body(3, "ABC-FOO-001 The device shall support IPv6."),    # req
        _body(4, "It shall also support IPv4."),                   # continuation of 001
        _body(5, "ABC-FOO-002 The device shall comply with ABC-FOO-001 dual-stack."),  # req + inline ref
        _heading(6, "1.2 Network Requirements"),
        _body(7, "Network section intro."),                        # preamble
        _body(8, "ABC-BAR-010 The network shall provide DNS."),    # req under new heading
    ]


def _parse(blocks, detection_mode="leading_id_body"):
    return GenericStructuralParser(_profile(detection_mode)).parse(_doc(blocks))


def _leading_reqs(tree):
    """Requirements created by leading-id detection: a req_id and no own
    section_number (section headings keep their section_number)."""
    return [r for r in tree.requirements if r.req_id and not r.section_number]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLeadingIdDetection:
    def test_each_leading_id_paragraph_becomes_a_requirement(self):
        tree = _parse(_mnob_blocks())
        ids = {r.req_id for r in _leading_reqs(tree)}
        assert ids == {"ABC-FOO-001", "ABC-FOO-002", "ABC-BAR-010"}

    def test_inline_reference_does_not_spawn_phantom(self):
        # ABC-FOO-002's body mentions ABC-FOO-001 mid-sentence; only three
        # leading-id requirements exist (no phantom for the inline mention).
        tree = _parse(_mnob_blocks())
        assert len(_leading_reqs(tree)) == 3

    def test_requirement_parented_to_enclosing_heading(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.section_number == ""          # not a section of its own
        assert foo1.parent_section == "1.1"        # the enclosing subsection

    def test_continuation_block_appends_to_requirement(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert "support IPv6" in foo1.text
        assert "support IPv4" in foo1.text         # the continuation block

    def test_preamble_lands_on_heading_not_a_requirement(self):
        tree = _parse(_mnob_blocks())
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert "covers device behavior" in sub.text
        # the preamble text is not a leading-id requirement
        assert all(
            "covers device behavior" not in r.text for r in _leading_reqs(tree)
        )

    def test_heading_reset_reparents_after_new_section(self):
        # ABC-BAR-010 sits under 1.2 — the cursor reset on the 1.2 heading
        # must reparent it there, not leave it under 1.1.
        tree = _parse(_mnob_blocks())
        bar = next(r for r in tree.requirements if r.req_id == "ABC-BAR-010")
        assert bar.parent_section == "1.2"

    def test_hierarchy_path_inherited_from_parent_heading(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.hierarchy_path == ["General", "Device Requirements"]

    def test_heading_nodes_have_no_req_id(self):
        # Section headings are non-requirement context in this mode.
        tree = _parse(_mnob_blocks())
        for r in tree.requirements:
            if r.section_number:
                assert r.req_id == ""


class TestPerRequirementPlanId:
    """D-DRAFT-1: each requirement carries its own ``plan_id`` extracted from
    its req_id (`<PREFIX>-<PLAN>-<DIGITS>`, plan at component position 1), so a
    single document carrying multiple plans is groupable downstream."""

    def test_each_requirement_gets_its_own_plan(self):
        tree = _parse(_mnob_blocks())
        plans = {r.req_id: r.plan_id for r in _leading_reqs(tree)}
        assert plans == {
            "ABC-FOO-001": "FOO",
            "ABC-FOO-002": "FOO",
            "ABC-BAR-010": "BAR",
        }

    def test_document_carries_multiple_plans(self):
        tree = _parse(_mnob_blocks())
        assert {r.plan_id for r in _leading_reqs(tree)} == {"FOO", "BAR"}

    def test_heading_nodes_fall_back_to_tree_plan(self):
        # Structural headings have no req_id → no extractable plan → they take
        # the tree-level plan_id (empty here, since the fixture sets no plan
        # metadata pattern). The point: population never crashes on id-less nodes.
        tree = _parse(_mnob_blocks())
        for r in tree.requirements:
            if r.section_number:  # a heading node
                assert r.plan_id == tree.plan_id


class TestDefaultHeadingModeUnchanged:
    """With the default ``detection_mode == "heading"``, leading-id body
    paragraphs do NOT become standalone requirements — they append to the
    enclosing heading and the first inline id becomes that heading's id.
    Guards the MNO-A (default) path against regression."""

    def test_no_standalone_leading_requirements(self):
        tree = _parse(_mnob_blocks(), detection_mode="heading")
        # No section_number-less req nodes were created by leading-id logic.
        assert _leading_reqs(tree) == []

    def test_inline_id_attaches_to_heading(self):
        tree = _parse(_mnob_blocks(), detection_mode="heading")
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        # Heading 1.1 had no id; the first body id is captured as its req_id.
        assert sub.req_id == "ABC-FOO-001"
