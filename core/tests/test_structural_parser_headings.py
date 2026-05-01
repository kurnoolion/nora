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


# ---------------------------------------------------------------------------
# FR-34: TOC omission
# ---------------------------------------------------------------------------


def _block_on_page(idx: int, page: int, text: str, *, size: float = 12.0, bold: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=bold),
    )


def test_toc_block_dropped_when_matches_pattern():
    """A single paragraph that looks like a TOC entry (leader dots + page
    number) is dropped."""
    blocks = [
        _block_on_page(0, 1, "1 Real Heading"),
        _block_on_page(1, 1, "1.1 Introduction ........... 5"),  # TOC line
        _block_on_page(2, 1, "real body content"),
    ]
    tree = _parse(blocks)
    nums = [r.section_number for r in tree.requirements]
    assert "1.1" not in nums, "TOC entry should not become a heading"
    assert "1" in nums
    assert tree.parse_stats.toc_blocks_dropped == 1


def test_toc_page_dropped_wholesale_when_threshold_met():
    """When ≥80% of a page's paragraph blocks are TOC entries, the entire
    page is dropped — including any non-matching blocks (e.g. the page
    header 'Table of Contents'), since they're part of the TOC zone."""
    # Page 2 = pure TOC: 1 header + 4 entries (header doesn't match leader
    # dots so technically 4/5 = 80% match, exactly at threshold).
    blocks = [
        _block_on_page(0, 1, "1 Real Top"),
        _block_on_page(1, 1, "intro body"),
        # Page 2 — 4 TOC entries + 1 header. 4/5 = 80% matches the default threshold.
        _block_on_page(2, 2, "Table of Contents"),
        _block_on_page(3, 2, "1 Real Top ........... 1"),
        _block_on_page(4, 2, "1.1 Sub ........... 2"),
        _block_on_page(5, 2, "1.1.1 Subsub ........... 3"),
        _block_on_page(6, 2, "2 Next ........... 4"),
        # Real content resumes on page 3.
        _block_on_page(7, 3, "1.1 Sub heading"),
        _block_on_page(8, 3, "sub body"),
    ]
    tree = _parse(blocks)
    nums = [r.section_number for r in tree.requirements]
    assert "1" in nums
    assert "1.1" in nums
    # TOC entries' "1.1" / "1.1.1" / "2" did not leak as headings:
    # 1.1 IS in the tree but came from page 3, not page 2. Confirm by
    # checking we don't have spurious extras.
    assert "1.1.1" not in nums
    assert "2" not in nums
    # 5 blocks on page 2 dropped wholesale:
    assert tree.parse_stats.toc_blocks_dropped == 5


def test_no_toc_when_pattern_doesnt_match():
    """A single TOC-like paragraph mixed in with real content shouldn't
    trigger page-level drop — only the matching block goes."""
    blocks = [
        _block_on_page(0, 1, "1 Real Top"),
        _block_on_page(1, 1, "real body content here"),
        _block_on_page(2, 1, "more body content"),
        _block_on_page(3, 1, "Some entry ........... 47"),  # only this drops
    ]
    tree = _parse(blocks)
    assert tree.parse_stats.toc_blocks_dropped == 1
    assert "1" in [r.section_number for r in tree.requirements]


def test_toc_detection_disabled_when_pattern_empty():
    """If profile.toc_detection_pattern is empty, no blocks are dropped
    and parse_stats.toc_blocks_dropped stays 0."""
    profile = _profile()
    profile.toc_detection_pattern = ""
    blocks = [
        _block_on_page(0, 1, "1 Real Top"),
        _block_on_page(1, 1, "1.1 Introduction ........... 5"),  # would normally match
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.parse_stats.toc_blocks_dropped == 0
    # With detection off, the TOC-shaped paragraph reaches the heading
    # classifier — depending on length/punctuation guards it may or may
    # not become a heading; we just assert no drop happened.


# ---------------------------------------------------------------------------
# FR-31: priority-marker extraction
# ---------------------------------------------------------------------------


def _profile_with_priority(pattern: str) -> DocumentProfile:
    p = _profile()
    p.heading_detection.priority_marker_pattern = pattern
    return p


def test_priority_marker_extracted_from_bracketed_form():
    """Heading like '1.2 [MANDATORY] Foo' → priority='MANDATORY', title='Foo'."""
    profile = _profile_with_priority(r"\[(MANDATORY|OPTIONAL|CONDITIONAL)\]")
    blocks = [_block(0, "1.2 [MANDATORY] Hardware Specs")]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "1.2")
    assert sec.priority == "MANDATORY"
    assert sec.title == "Hardware Specs", f"unexpected title: {sec.title!r}"


def test_priority_marker_uppercased_regardless_of_input_case():
    """Lowercase 'optional' marker → uppercased priority value."""
    profile = _profile_with_priority(r"\((mandatory|optional|conditional)\)")
    blocks = [_block(0, "2.3 Foo Bar (optional)")]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "2.3")
    assert sec.priority == "OPTIONAL"
    assert sec.title == "Foo Bar"


def test_no_priority_when_pattern_unset():
    """Empty priority_marker_pattern → priority stays empty string for all sections."""
    profile = _profile()  # default has empty pattern
    assert profile.heading_detection.priority_marker_pattern == ""
    blocks = [
        _block(0, "1.2 [MANDATORY] Foo"),  # marker text present but profile doesn't ask
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "1.2")
    assert sec.priority == ""
    # Title preserves the bracketed text since we don't know to strip it.
    assert "[MANDATORY]" in sec.title


def test_no_priority_when_pattern_set_but_text_doesnt_match():
    """Pattern set, but heading has no marker → priority empty, title untouched."""
    profile = _profile_with_priority(r"\[(MANDATORY|OPTIONAL)\]")
    blocks = [_block(0, "3.1 Plain Heading No Marker")]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "3.1")
    assert sec.priority == ""
    assert sec.title == "Plain Heading No Marker"


# ---------------------------------------------------------------------------
# FR-33: strikeout content omission
# ---------------------------------------------------------------------------


def _struck_block(idx: int, text: str, *, size: float = 12.0, bold: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=bold, strikethrough=True),
    )


def test_struck_block_dropped_by_default():
    """ignore_strikeout=True (default) → struck blocks are skipped before
    heading classification, and parse_stats.struck_blocks_dropped is bumped."""
    blocks = [
        _block(0, "1 Real Heading"),
        _struck_block(1, "1.1 Deleted Subsection"),  # struck — should NOT become a heading
        _block(2, "real body text"),
    ]
    tree = _parse(blocks)
    nums = [r.section_number for r in tree.requirements]
    assert "1.1" not in nums, "struck block should not become a heading"
    assert "1" in nums
    assert tree.parse_stats.struck_blocks_dropped == 1


def test_struck_block_kept_when_override_disabled():
    """When profile.ignore_strikeout=False, struck blocks reach the parser
    untouched and the counter stays at 0."""
    profile = _profile()
    profile.ignore_strikeout = False
    blocks = [
        _block(0, "1 Real Heading"),
        _struck_block(1, "1.1 Kept-Despite-Strike Subsection"),
        _block(2, "body"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    nums = [r.section_number for r in tree.requirements]
    assert "1.1" in nums, "struck block should classify as heading when override disabled"
    assert tree.parse_stats.struck_blocks_dropped == 0


def test_struck_block_without_font_info_is_not_dropped():
    """A block without font_info has no strikethrough signal — must not be
    spuriously dropped (the `block.font_info is not None` guard matters)."""
    blocks = [
        _block(0, "1 Real Heading"),
        ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=1),
            text="body line without font_info",
            font_info=None,
        ),
    ]
    tree = _parse(blocks)
    assert tree.parse_stats.struck_blocks_dropped == 0
    # Body content should reach current section (the "1 Real Heading" req).
    sec = next(r for r in tree.requirements if r.section_number == "1")
    assert "body line without font_info" in sec.text
