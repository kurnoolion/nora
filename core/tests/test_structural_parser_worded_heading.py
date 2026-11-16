"""Worded heading numbers (strand history-fidelity).

Field shape: the profile's ``numbering_pattern`` admits a chapter heading of
the form ``Chapter <N> - <title>``. The classifier re-read the number with a
digit-first regex, rejected the line, and the chapter title plus its
introductory prose folded into the previous chapter's last requirement. The
number now comes from the profile match when the digit-first regex fails.

Generic example ids only (``ABC-FOO-001``).
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

_CHAPTER_PATTERN = r"^(?:Chapter\s+(\d+)|(\d+(?:\.\d+)+))\.?(?=\s)"


def _profile(pattern: str = _CHAPTER_PATTERN) -> DocumentProfile:
    return DocumentProfile(
        profile_name="worded_heading_test",
        profile_version=1,
        created_from=[],
        last_updated="2026-11-16",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[],
            numbering_pattern=pattern,
            max_observed_depth=4,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"ABC-[A-Z]+-\d+",
            components={"separator": "-", "plan_id_position": 1, "number_position": 2},
            anchor="leading_text",
            detection_mode="leading_id_body",
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _block(text: str, size: float = 12.0) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
        text=text, font_info=FontInfo(size=size, bold=size > 12.0),
    )


def _parse(blocks, profile=None):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf",
                    mno="MNOX", release="R2", content_blocks=blocks)
    return GenericStructuralParser(profile or _profile()).parse(ir)


def _blocks():
    return [
        _block("Chapter 1 - General", 14.0),
        _block("1.1 Device", 13.0),
        _block("ABC-FOO-001 The device shall do alpha."),
        _block("Chapter 2 - Radio", 14.0),
        _block("This chapter covers the radio."),
        _block("2.1 Bands", 13.0),
        _block("ABC-FOO-002 The device shall do beta."),
    ]


def _req(tree, rid):
    return next(r for r in tree.requirements if r.req_id == rid)


def test_chapter_heading_becomes_top_level_section():
    tree = _parse(_blocks())
    nums = {r.section_number for r in tree.requirements}
    assert {"1", "2"} <= nums


def test_chapter_preamble_stays_out_of_previous_requirement():
    tree = _parse(_blocks())
    alpha = _req(tree, "ABC-FOO-001")
    assert "Chapter 2" not in alpha.text
    assert "covers the radio" not in alpha.text
    chapter2 = next(r for r in tree.requirements if r.section_number == "2")
    assert "covers the radio" in chapter2.text


def test_dotted_numbers_unchanged():
    tree = _parse(_blocks())
    assert _req(tree, "ABC-FOO-002").text.endswith("The device shall do beta.")
    assert any(r.section_number == "2.1" for r in tree.requirements)


def test_pattern_without_worded_prefix_still_rejects_it():
    tree = _parse(_blocks(), _profile(r"^(\d+(?:\.\d+)+)\.?(?=\s)"))
    assert not any(r.section_number in ("1", "2") for r in tree.requirements)
