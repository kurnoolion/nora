"""Tests for the numbering-driven heading classification contract.

Numbering is the necessary signal for heading detection; style/font is
advisory only. These tests exercise that behavior with hand-crafted
in-memory blocks.
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


def _profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="test",
        profile_version=1,
        created_from=[],
        last_updated="2026-04-29",
        heading_detection=HeadingDetection(
            method="numbering",
            # Single advisory style rule — parser should ignore it for
            # classification gating when method == "numbering".
            levels=[
                HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True),
            ],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
        ),
        requirement_id=RequirementIdPattern(),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _block(idx: int, text: str, *, size: float = 12.0, bold: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=bold),
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
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


def _parse(blocks):
    return GenericStructuralParser(_profile()).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# Style is a hint, not a gate
# ---------------------------------------------------------------------------


def test_heading_classified_when_style_misses_but_numbering_matches():
    """A block with body-text font but a section-number prefix is still a heading."""
    blocks = [
        _block(0, "2.1 Foo Heading In Body Font", size=12.0, bold=False),
        _block(1, "body content under foo"),
    ]
    tree = _parse(blocks)
    sections = [r.section_number for r in tree.requirements]
    assert "2.1" in sections, f"Expected 2.1 to be classified as heading; got {sections}"


def test_top_level_no_dot_classified_as_depth_one():
    """Top-level chapter headings without trailing dot ('2 LTE') must classify."""
    blocks = [
        _block(0, "2 LTE Data Retry"),
        _block(1, "some intro"),
        _block(2, "2.1 INTRODUCTION"),
        _block(3, "intro text"),
    ]
    tree = _parse(blocks)
    sections = {r.section_number: r for r in tree.requirements}
    assert "2" in sections, "Top-level '2 LTE Data Retry' must be a heading"
    assert "2.1" in sections
    # Hierarchy: 2.1's parent should be 2.
    assert sections["2.1"].parent_section == "2"


def test_deep_nesting_assigns_parent_chain_via_numbering():
    """2.1.1.1 → parent 2.1.1, grandparent 2.1, root 2."""
    blocks = [
        _block(0, "2 Top"),
        _block(1, "2.1 Sub"),
        _block(2, "2.1.1 Sub-sub"),
        _block(3, "2.1.1.1 Leaf"),
    ]
    tree = _parse(blocks)
    sections = {r.section_number: r for r in tree.requirements}
    assert sections["2.1.1.1"].parent_section == "2.1.1"
    assert sections["2.1.1"].parent_section == "2.1"
    assert sections["2.1"].parent_section == "2"
    # hierarchy_path follows section_number depth.
    assert len(sections["2.1.1.1"].hierarchy_path) >= 1


# ---------------------------------------------------------------------------
# False-positive guards
# ---------------------------------------------------------------------------


def test_long_numbered_body_paragraph_not_classified():
    """A body sentence starting with '1. The system shall ...' that runs long
    and ends with a period is not a heading."""
    long_body = (
        "1. The system shall ensure that whenever the device transitions "
        "from RRC_CONNECTED to RRC_IDLE under a particular set of network "
        "conditions, the resulting state is reported to the upper layers "
        "without delay, and any pending requirements are honored as "
        "specified in the related cross-references."
    )
    blocks = [
        _block(0, "1 Real Top"),
        _block(1, long_body),
    ]
    tree = _parse(blocks)
    section_numbers = [r.section_number for r in tree.requirements]
    # "1" should be the only heading; "1." style numbered list item rejected.
    assert section_numbers == ["1"], (
        f"Expected only '1' as heading; got {section_numbers}"
    )


def test_numbered_list_item_with_trailing_period_not_classified():
    """A long-ish text starting with '1. Foo bar.' (terminal period) is body, not heading."""
    blocks = [
        _block(0, "1 Top"),
        _block(
            1,
            "1. Initialize the resource manager and report status to the orchestrator.",
        ),
    ]
    tree = _parse(blocks)
    section_numbers = [r.section_number for r in tree.requirements]
    assert section_numbers == ["1"]


def test_duplicate_section_number_demoted_to_body():
    """If two blocks both classify as section '1.3.1', only the first wins; the
    second is treated as body text appended to the previous section."""
    blocks = [
        _block(0, "1.3.1 Real Heading"),
        _block(1, "real heading body"),
        _block(2, "1.3.1 Stray duplicate-numbered phrase"),  # demoted
    ]
    tree = _parse(blocks)
    nums = [r.section_number for r in tree.requirements]
    assert nums.count("1.3.1") == 1, f"Duplicate section numbers leaked: {nums}"


# ---------------------------------------------------------------------------
# Style hint preserved in profile but parser doesn't gate on it
# ---------------------------------------------------------------------------


def test_advisory_style_rule_does_not_gate_classification():
    """Profile carries one advisory HeadingLevel (size 13–15, bold) but a
    plain body-styled (size 12, not bold) numbered block is still a heading."""
    profile = _profile()
    assert profile.heading_detection.levels  # advisory rule present
    advisory = profile.heading_detection.levels[0]
    assert advisory.font_size_min == 13.0  # confirms style rule wouldn't match...

    # ...but body-styled block with numbering still classifies:
    blocks = [_block(0, "5.2 Style mismatch but numbered", size=12.0, bold=False)]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert any(r.section_number == "5.2" for r in tree.requirements)
