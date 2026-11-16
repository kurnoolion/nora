"""Fused headings in heading mode (strand history-fidelity).

Field shape: the PDF extractor joined a requirement heading
(``<outline number> <title> (Marker) ID: <id>``) onto the end of the
previous requirement's last body paragraph, with a single space between
them. The heading never anchored, and its requirement's text folded into the
previous requirement. The parser now splits such a paragraph before the
embedded outline number when an id label follows within heading length and
the number is the plausible next heading.

(Tags GP/REQ/SEC are generic test values.)
"""

from __future__ import annotations

from core.src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
    TextRun,
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


def _profile(id_label: bool = True) -> DocumentProfile:
    return DocumentProfile(
        profile_name="fused_heading_test",
        profile_version=1,
        created_from=[],
        last_updated="2026-11-16",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=6,
            priority_marker_pattern=r"\((Mandatory|Optional)\)",
            require_heading_font_for_numbering=True,
            priority_requires_req_id=True,
            title_strip_pattern=r"\s*ID:\s*\S+\s*$",
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"GP-(?:SEC|REQ)-\d+",
            requirement_type_pattern=r"GP-REQ-\d+",
            id_label_pattern=r"ID:\s*(GP-(?:SEC|REQ)-\d+)" if id_label else "",
            detection_mode="heading",
            anchor="leading_text",
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=9.0, font_size_max=10.0),
    )


def _head(text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
        text=text, font_info=FontInfo(size=12.0, bold=True),
    )


def _body(text: str, struck: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH, position=Position(page=1, index=0),
        text=text, font_info=FontInfo(size=9.5, bold=False),
        runs=[TextRun(text=text, struck=struck)],
    )


def _parse(blocks: list[ContentBlock], profile: DocumentProfile | None = None):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf",
                    mno="MNOX", release="R2", content_blocks=blocks)
    return GenericStructuralParser(profile or _profile()).parse(ir)


def _by_id(tree, rid):
    hits = [r for r in tree.requirements if r.req_id == rid]
    return hits[0] if hits else None


_PREFIX = [
    _head("3 Requirements"),
    _head("3.2 Device behaviour ID: GP-SEC-1"),
    _head("3.2.1 Alpha (Mandatory) ID: GP-REQ-1"),
]


def test_heading_fused_after_sentence_is_split():
    tree = _parse(_PREFIX + [
        _body("The device shall do alpha. 3.2.2 Beta (Mandatory) ID: GP-REQ-2"),
        _body("The device shall do beta."),
    ])
    alpha, beta = _by_id(tree, "GP-REQ-1"), _by_id(tree, "GP-REQ-2")
    assert alpha.text == "The device shall do alpha."
    assert beta is not None
    assert beta.section_number == "3.2.2"
    assert beta.title == "Beta"
    assert beta.priority == "MANDATORY"
    assert beta.text == "The device shall do beta."
    assert tree.parse_stats.fused_headings_split == 1


def test_heading_fused_after_word_with_body_in_same_block():
    tree = _parse(_PREFIX + [
        _body("alpha holds in idle 3.2.2 Beta (Mandatory) ID: GP-REQ-2 The device shall do beta."),
    ])
    assert _by_id(tree, "GP-REQ-1").text == "alpha holds in idle"
    assert _by_id(tree, "GP-REQ-2").text == "The device shall do beta."


def test_two_fused_headings_in_one_block():
    tree = _parse(_PREFIX + [
        _body("Alpha body. 3.2.2 Beta ID: GP-REQ-2 Beta body. 3.2.3 Gamma ID: GP-REQ-3 Gamma body."),
    ])
    assert _by_id(tree, "GP-REQ-1").text == "Alpha body."
    assert _by_id(tree, "GP-REQ-2").text == "Beta body."
    assert _by_id(tree, "GP-REQ-3").text == "Gamma body."
    assert tree.parse_stats.fused_headings_split == 2


def test_child_and_ancestor_sibling_numbers_split():
    tree = _parse(_PREFIX + [
        _body("Alpha body. 3.2.1.1 Child ID: GP-REQ-4"),
        _body("Child body. 3.3 Next section ID: GP-SEC-2"),
    ])
    assert _by_id(tree, "GP-REQ-4").section_number == "3.2.1.1"
    assert any(r.section_number == "3.3" for r in tree.requirements)
    assert tree.parse_stats.fused_headings_split == 2


def test_citation_from_another_branch_does_not_split():
    tree = _parse(_PREFIX + [
        _body("Alpha body, per TS 24.301 ID: GP-REQ-9 as amended."),
        _body("Also see 3.1.4 Earlier ID: GP-REQ-8 for context."),
    ])
    assert _by_id(tree, "GP-REQ-9") is None
    assert _by_id(tree, "GP-REQ-8") is None
    assert "24.301" in _by_id(tree, "GP-REQ-1").text
    assert tree.parse_stats.fused_headings_split == 0


def test_label_too_far_from_number_does_not_split():
    filler = "word " * 60
    tree = _parse(_PREFIX + [
        _body(f"Alpha body. 3.2.2 {filler}ID: GP-REQ-2"),
    ])
    assert _by_id(tree, "GP-REQ-2") is None
    assert tree.parse_stats.fused_headings_split == 0


def test_struck_block_is_not_split():
    tree = _parse(_PREFIX + [
        _body("Alpha body. 3.2.2 Beta ID: GP-REQ-2", struck=True),
    ])
    assert _by_id(tree, "GP-REQ-2") is None
    assert tree.parse_stats.fused_headings_split == 0


def test_without_id_label_nothing_splits():
    prof = _profile(id_label=False)
    tree = _parse([
        _head("3 Requirements"),
        _head("3.2.1 Alpha GP-REQ-1"),
        _body("Alpha body. 3.2.2 Beta ID: GP-REQ-2"),
    ], prof)
    assert tree.parse_stats.fused_headings_split == 0
    assert not any(r.section_number == "3.2.2" for r in tree.requirements)


def test_unfused_heading_unchanged():
    tree = _parse(_PREFIX + [
        _body("Alpha body."),
        _head("3.2.2 Beta (Mandatory) ID: GP-REQ-2"),
        _body("Beta body."),
    ])
    assert _by_id(tree, "GP-REQ-1").text == "Alpha body."
    assert _by_id(tree, "GP-REQ-2").text == "Beta body."
    assert tree.parse_stats.fused_headings_split == 0
