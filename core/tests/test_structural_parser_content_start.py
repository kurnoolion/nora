"""Tests for the content-start cutoff (D-DRAFT-4).

A profile-driven cutoff that drops every block before the first *real heading*
(heading-level font) whose top-level section number == `content_start_section`,
skipping the front page / TOC / intro chapters in one positive anchor. The
font gate is what makes a same-numbered TOC entry (body-size) NOT trigger it.

Hand-crafted in-memory IR + profile fixtures. Generic ids only.
"""

from __future__ import annotations

from core.src.models.document import (
    BlockType, ContentBlock, DocumentIR, FontInfo, Position,
)
from core.src.parser.structural_parser import GenericStructuralParser
from core.src.profiler.profile_schema import (
    BodyText, CrossReferencePatterns, DocumentProfile, HeaderFooter,
    HeadingDetection, HeadingLevel, PlanMetadata, RequirementIdPattern,
)


def _profile(content_start: str = "3") -> DocumentProfile:
    return DocumentProfile(
        profile_name="test-content-start",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True)],
            numbering_pattern=r"^\d+(\.\d+)*\s",
            max_observed_depth=3,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"ABC-[A-Z]+-\d+",
            components={"separator": "-", "plan_id_position": 1, "number_position": 2},
            anchor="leading_text",
            detection_mode="leading_id_body",
        ),
        plan_metadata=PlanMetadata(),
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
        content_start_section=content_start,
    )


def _heading(text: str) -> ContentBlock:  # bold + heading size
    return ContentBlock(type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
                        text=text, font_info=FontInfo(size=14.0, bold=True))


def _body(text: str) -> ContentBlock:
    return ContentBlock(type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
                        text=text, font_info=FontInfo(size=12.0, bold=False))


def _toc_entry(text: str) -> ContentBlock:  # body-size, NOT bold (looks like a heading by text only)
    return ContentBlock(type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
                        text=text, font_info=FontInfo(size=12.0, bold=False))


def _doc(blocks):
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOB",
                      release="Jun2026", doc_type="requirement", content_blocks=blocks)


# Front page -> TOC (incl. a body-font "3" entry) -> Ch.1 -> Ch.2 -> Ch.3 -> Ch.4
def _blocks():
    return [
        _body("Device Requirements Specification"),     # 0 front page
        _toc_entry("3 Requirements ........... 12"),     # 1 TOC entry for ch.3 (body font!)
        _heading("1 Preface"),                           # 2 ch.1 heading
        _body("This document describes ..."),            # 3 ch.1 body
        _heading("2 Scope"),                             # 4 ch.2 heading
        _body("Applies to devices ..."),                 # 5 ch.2 body
        _heading("3 Requirements"),                      # 6 ch.3 heading  <- content start
        _heading("3.1 Device"),                          # 7
        _body("ABC-FOO-001 The device shall support IPv6."),  # 8 req
        _heading("4 Network"),                           # 9
        _body("ABC-BAR-010 The network shall provide DNS."),  # 10 req
    ]


def _parse(content_start="3"):
    return GenericStructuralParser(_profile(content_start)).parse(_doc(_blocks()))


def _leading_reqs(tree):
    return [r for r in tree.requirements if r.req_id and not r.section_number]


class TestContentStartCutoff:
    def test_tree_starts_at_chapter_3(self):
        tree = _parse(content_start="3")
        nums = {r.section_number for r in tree.requirements if r.section_number}
        # ch.3/3.1/4 kept; ch.1/ch.2 dropped
        assert "3" in nums and "3.1" in nums and "4" in nums
        assert "1" not in nums and "2" not in nums

    def test_intro_chapter_text_dropped(self):
        tree = _parse(content_start="3")
        allbodies = " ".join(r.text for r in tree.requirements)
        assert "Preface" not in allbodies and "Applies to devices" not in allbodies

    def test_requirements_after_cutoff_kept(self):
        tree = _parse(content_start="3")
        assert {r.req_id for r in _leading_reqs(tree)} == {"ABC-FOO-001", "ABC-BAR-010"}

    def test_font_gating_toc_entry_does_not_trigger_early(self):
        # The body-font TOC entry "3 ..." sits at index 1, BEFORE ch.1/ch.2.
        # If it had triggered the cutoff, ch.1/ch.2 (indices 2-5) would survive.
        # They don't -> the cutoff fired at the real ch.3 heading (index 6).
        tree = _parse(content_start="3")
        nums = {r.section_number for r in tree.requirements if r.section_number}
        assert "1" not in nums and "2" not in nums

    def test_disabled_by_default_keeps_everything(self):
        tree = _parse(content_start="")
        nums = {r.section_number for r in tree.requirements if r.section_number}
        # no cutoff -> ch.1 and ch.2 headings present too
        assert "1" in nums and "2" in nums and "3" in nums
