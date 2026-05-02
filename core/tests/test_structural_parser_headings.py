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
# No-whitespace heading variant (PDF-extraction artifact)
# ---------------------------------------------------------------------------


def _profile_no_space_heading() -> DocumentProfile:
    """Profile with the relaxed numbering pattern that accepts no-space
    title runs (matches what the profiler emits today). Top-level numbers
    still require whitespace; only multi-dot section numbers may run
    directly into an uppercase title (PDF-extraction artifact)."""
    p = _profile()
    p.heading_detection.numbering_pattern = (
        r"^(?:(\d+)(?=\s)|(\d+(?:\.\d+)+)(?=\s|[A-Z]))"
    )
    return p


def test_heading_with_no_space_before_title_classified():
    """PyMuPDF text extraction can drop the space between section number
    and title for bold-rendered headings (e.g. `1.4.3.1.1.5EMM Cause Code 19`).
    The relaxed gate must classify these so subsections aren't silently lost."""
    profile = _profile_no_space_heading()
    blocks = [
        _block(0, "1.4.3.1.1 PARENT SECTION HEADING"),
        _block(1, "1.4.3.1.1.5EMM Cause Code 19"),  # no space between number and title
        _block(2, "body text under 1.4.3.1.1.5"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(
        (r for r in tree.requirements if r.section_number == "1.4.3.1.1.5"),
        None,
    )
    assert sec is not None, (
        f"Expected section 1.4.3.1.1.5 to be classified; got "
        f"{[r.section_number for r in tree.requirements]}"
    )
    assert sec.title == "EMM Cause Code 19", f"unexpected title: {sec.title!r}"


def test_two_digit_final_segment_no_space_classified():
    """Same fix must work for two-digit final segments — e.g.
    `1.4.3.1.1.10Upon receipt` (no space, two-digit `.10`)."""
    profile = _profile_no_space_heading()
    blocks = [
        _block(0, "1.4.3.1.1 PARENT"),
        _block(1, "1.4.3.1.1.10Upon receipt of an ATTACH REJECT"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(
        (r for r in tree.requirements if r.section_number == "1.4.3.1.1.10"),
        None,
    )
    assert sec is not None
    assert sec.title.startswith("Upon receipt"), f"unexpected title: {sec.title!r}"


def test_no_space_lowercase_following_rejected():
    """Section number followed directly by a LOWERCASE letter (no space)
    must NOT classify — these are body-text artifacts like '5.0gnetwork'
    or rare PDF cases where a sentence runs into a number. Uppercase
    enforces the title-case heading convention."""
    profile = _profile_no_space_heading()
    blocks = [
        _block(0, "1 Real Top"),
        _block(1, "5.0gnetwork bandwidth note"),  # lowercase after — body text
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    nums = [r.section_number for r in tree.requirements]
    assert "5.0" not in nums, "no-space-lowercase variant should not classify"
    assert "1" in nums


def test_top_level_no_space_uppercase_rejected_3gpp_case():
    """Body text starting with `3GPP TS 24.301` MUST NOT be misread as
    section `3` with title `GPP TS 24.301`. The fix: top-level (no-dot)
    numbers require whitespace; only multi-dot numbers may run directly
    into an uppercase title."""
    profile = _profile_no_space_heading()
    blocks = [
        _block(0, "1 Real Top"),
        _block(1, "3GPP TS 24.301 specifies the EMM cause codes."),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    nums = [r.section_number for r in tree.requirements]
    assert "3" not in nums, "'3GPP' must not classify as section 3 heading"
    assert "1" in nums


# ---------------------------------------------------------------------------
# Phantom-section replace on duplicate section_number (TOC residual defense)
# ---------------------------------------------------------------------------


def test_phantom_section_replaced_by_real_heading():
    """If a previous heading classification created an "empty" section
    (no req_id, no body, no children) — typically a TOC entry that
    slipped past the TOC drop — and a later real heading with the same
    section_number arrives, the real heading must REPLACE the phantom
    rather than be demoted to body text. Demotion would shift req_id
    assignment by one slot and cascade through the entire subtree."""
    profile = _profile_no_space_heading()
    # Simulate: phantom heading first (no following req_id, no body),
    # then later the real heading with a req_id following it.
    phantom_block = _block(0, "1.1.1 Phantom — TOC residual")
    real_block = _block(1, "1.1.1 Real Heading")
    real_id = ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=2, index=2),
        text="VZ_REQ_TEST_42",
        font_info=FontInfo(size=7.0, bold=True),  # small font = req_id block
    )
    body = _block(3, "real body content under 1.1.1", size=12.0)
    blocks = [phantom_block, real_block, real_id, body]

    # Profile needs requirement_id pattern + body_text for req_id-block detection.
    profile.requirement_id = RequirementIdPattern(pattern=r"VZ_REQ_[A-Z0-9_]+_\d+")
    profile.body_text = BodyText(font_size_min=11.0, font_size_max=12.0)

    tree = GenericStructuralParser(profile).parse(_doc(blocks))

    # Exactly one section 1.1.1 — the real one, not the phantom.
    sec_111 = [r for r in tree.requirements if r.section_number == "1.1.1"]
    assert len(sec_111) == 1, f"Expected 1 section 1.1.1, got {len(sec_111)}"
    sec = sec_111[0]
    assert sec.title == "Real Heading", f"unexpected title {sec.title!r}"
    assert sec.req_id == "VZ_REQ_TEST_42", (
        f"req_id should attach to the REAL heading, got {sec.req_id!r}"
    )
    assert "real body content" in sec.text


def test_extra_req_id_in_section_does_not_lateral_to_next_heading():
    """OA convention: req_ids are TRAILING markers — they belong to the
    section they appear in. If a section already has a req_id and another
    small-font id appears (artifact, footer, duplicate), it must NOT be
    lateralled to the next heading via `pending_req_id`. Otherwise every
    subsequent heading inherits the wrong id and the assignment cascade
    is off by one across the whole subtree."""
    profile = _profile_no_space_heading()
    profile.requirement_id = RequirementIdPattern(pattern=r"VZ_REQ_[A-Z0-9_]+_\d+")
    profile.body_text = BodyText(font_size_min=11.0, font_size_max=12.0)

    sec_a_heading = _block(0, "1 Section A")
    sec_a_id = ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=1),
        text="VZ_REQ_TEST_100",
        font_info=FontInfo(size=7.0, bold=True),
    )
    sec_a_extra = ContentBlock(  # extra id — must NOT lateral
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=2),
        text="VZ_REQ_TEST_999",
        font_info=FontInfo(size=7.0, bold=True),
    )
    sec_b_heading = _block(3, "2 Section B")
    sec_b_id = ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=4),
        text="VZ_REQ_TEST_200",
        font_info=FontInfo(size=7.0, bold=True),
    )
    blocks = [sec_a_heading, sec_a_id, sec_a_extra, sec_b_heading, sec_b_id]

    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    secs = {r.section_number: r.req_id for r in tree.requirements}
    assert secs.get("1") == "VZ_REQ_TEST_100", (
        f"Section 1 must keep its first req_id; got {secs.get('1')!r}"
    )
    assert secs.get("2") == "VZ_REQ_TEST_200", (
        f"Section 2 must get its OWN trailing id, not lateralled _999; "
        f"got {secs.get('2')!r}"
    )


def test_real_duplicate_section_still_demoted():
    """If a duplicate section_number arrives but the existing section
    already has a req_id (real, not phantom), the duplicate is still
    demoted to body text per the existing invariant."""
    profile = _profile_no_space_heading()
    profile.requirement_id = RequirementIdPattern(pattern=r"VZ_REQ_[A-Z0-9_]+_\d+")
    profile.body_text = BodyText(font_size_min=11.0, font_size_max=12.0)

    real_block = _block(0, "1.1.1 First Real Heading")
    real_id = ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=1),
        text="VZ_REQ_TEST_99",
        font_info=FontInfo(size=7.0, bold=True),
    )
    duplicate = _block(2, "1.1.1 Stray Duplicate")  # demoted; existing has req_id
    blocks = [real_block, real_id, duplicate]

    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec_111 = [r for r in tree.requirements if r.section_number == "1.1.1"]
    assert len(sec_111) == 1
    sec = sec_111[0]
    assert sec.title == "First Real Heading"
    assert sec.req_id == "VZ_REQ_TEST_99"
    # Duplicate's text appears as body content under the real section.
    assert "Stray Duplicate" in sec.text


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


# ---------------------------------------------------------------------------
# FR-32: form-factor applicability with hierarchical inheritance
# ---------------------------------------------------------------------------


def _profile_with_applicability(
    requirement_patterns: list[str] | None = None,
    global_section_pattern: str = "",
) -> DocumentProfile:
    p = _profile()
    from core.src.profiler.profile_schema import ApplicabilityDetection
    p.applicability_detection = ApplicabilityDetection(
        requirement_patterns=requirement_patterns or [],
        global_section_pattern=global_section_pattern,
    )
    return p


def test_applicability_explicit_value_extracted_from_section_text():
    """A section's own text matches `requirement_patterns` → labels populated."""
    profile = _profile_with_applicability(
        requirement_patterns=[r"[Aa]pplies to:?\s*([\w,\s]+?)(?:\.|\n|$)"]
    )
    blocks = [
        _block(0, "1 Hardware Specs"),
        _block(1, "1.1 Antennas"),
        _block(2, "Applies to: smartphones, tablets"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "1.1")
    assert sec.applicability == ["smartphones", "tablets"]


def test_applicability_inherited_from_parent_when_section_silent():
    """Child section without its own applicability text inherits parent's."""
    profile = _profile_with_applicability(
        requirement_patterns=[r"[Aa]pplies to:?\s*([\w,\s]+?)(?:\.|\n|$)"]
    )
    blocks = [
        _block(0, "2 Specs"),
        _block(1, "Applies to: smartphones"),
        _block(2, "2.1 Sub"),
        _block(3, "no applicability text here"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    parent = next(r for r in tree.requirements if r.section_number == "2")
    child = next(r for r in tree.requirements if r.section_number == "2.1")
    assert parent.applicability == ["smartphones"]
    assert child.applicability == ["smartphones"], "child must inherit"


def test_applicability_root_default_from_global_section():
    """When the document declares a top-level applicability section, its
    contents seed the root default for sections that have no own value
    and no resolvable parent."""
    profile = _profile_with_applicability(
        requirement_patterns=[r"[Aa]pplies to:?\s*([\w,\s]+?)(?:\.|\n|$)"],
        global_section_pattern=r"(?i)^applicability$",
    )
    blocks = [
        _block(0, "1 Applicability"),
        _block(1, "Applies to: smartphones, data devices"),
        _block(2, "2 Hardware"),
        _block(3, "no applicability here"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    section_2 = next(r for r in tree.requirements if r.section_number == "2")
    # Section 2 has no own applicability, no parent — falls back to root default.
    assert section_2.applicability == ["smartphones", "data devices"]


def test_applicability_empty_when_no_pattern_no_global_no_text():
    """Profile has no applicability rules → field stays empty everywhere."""
    profile = _profile()  # no applicability_detection patterns
    blocks = [
        _block(0, "1 Top"),
        _block(1, "no applicability statements anywhere"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    for r in tree.requirements:
        assert r.applicability == []


def test_applicability_dedupes_repeated_labels():
    """Repeated labels in capture group are deduplicated case-insensitively
    while preserving order."""
    profile = _profile_with_applicability(
        requirement_patterns=[r"[Aa]pplies to:?\s*([\w,\s]+?)(?:\.|\n|$)"]
    )
    blocks = [
        _block(0, "3 Top"),
        _block(1, "Applies to: smartphones, Smartphones, tablets"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    sec = next(r for r in tree.requirements if r.section_number == "3")
    # Case-insensitive dedup; first occurrence preserved.
    assert sec.applicability == ["smartphones", "tablets"]


# ---------------------------------------------------------------------------
# FR-35: definitions / acronyms extraction (parser side; chunk_builder
# expansion is exercised in tests/test_chunk_builder_definitions.py)
# ---------------------------------------------------------------------------


def test_definitions_extracted_from_glossary_section():
    """Section title matching the default `(?i)acronym|definition|glossary`
    pattern → entries parsed from body text."""
    profile = _profile()  # default definitions_section_pattern + entry_pattern
    body = (
        "ETWS - Earthquake and Tsunami Warning System\n"
        "SUPL - Secure User Plane Location\n"
        "RAT - Radio Access Technology"
    )
    blocks = [
        _block(0, "1 Top"),
        _block(1, "1.1 Acronyms"),
        _block(2, body),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.definitions_map == {
        "ETWS": "Earthquake and Tsunami Warning System",
        "SUPL": "Secure User Plane Location",
        "RAT": "Radio Access Technology",
    }
    assert tree.definitions_section_number == "1.1"
    assert tree.parse_stats.defs_extracted == 3


def test_definitions_first_occurrence_wins():
    """Duplicate term: first definition wins; later collisions ignored."""
    profile = _profile()
    body = (
        "ETWS - Earthquake and Tsunami Warning System\n"
        "ETWS - Some Other Expansion (should be ignored)"
    )
    blocks = [
        _block(0, "1 Glossary"),
        _block(1, body),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.definitions_map == {
        "ETWS": "Earthquake and Tsunami Warning System",
    }


def test_definitions_empty_when_section_pattern_unset():
    """Profile.heading_detection.definitions_section_pattern empty → no
    extraction, definitions_map stays empty, defs_extracted=0."""
    profile = _profile()
    profile.heading_detection.definitions_section_pattern = ""
    body = "ETWS - Earthquake and Tsunami Warning System"
    blocks = [
        _block(0, "1 Acronyms"),  # would normally match but pattern disabled
        _block(1, body),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.definitions_map == {}
    assert tree.parse_stats.defs_extracted == 0


def test_definitions_section_kept_in_parsed_tree():
    """The definitions section itself stays as a Requirement node so users
    can still query it directly — extraction is additive."""
    profile = _profile()
    blocks = [
        _block(0, "1 Acronyms"),
        _block(1, "ETWS - Earthquake and Tsunami Warning System"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert any(r.section_number == "1" for r in tree.requirements)


def test_definitions_long_prose_lines_not_misread():
    """Length cap on the term (16 chars) plus uppercase-leading guard
    prevents normal sentences from being read as entries."""
    profile = _profile()
    body = (
        "ETWS - Earthquake and Tsunami Warning System\n"
        "This is a long sentence that should not be parsed as an entry\n"
        "because the term capture only matches a uppercase short token."
    )
    blocks = [
        _block(0, "1 Acronyms"),
        _block(1, body),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert "ETWS" in tree.definitions_map
    # No prose-as-entry false positives:
    assert "This is a long sentence" not in tree.definitions_map
    assert len(tree.definitions_map) == 1
