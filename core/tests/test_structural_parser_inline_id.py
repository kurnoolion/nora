"""Inline-trailing req_id in heading text (mno-c-ingestion).

Some corpora put the requirement id INSIDE the heading paragraph, trailing,
behind an `ID:` label and an optional `(priority)` marker — e.g.
`4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345`. The req_id
type tag (`REQ` here) also discriminates an actual requirement from a structural
sub-section (`SEC`): `requirement_id.pattern` is scoped to the requirement type,
so only those headings carry a req_id. `heading_detection.title_strip_pattern`
removes the trailing `ID: <id>` from EVERY heading title so both requirement and
structural titles are clean.

(Tags GP/REQ/SEC are generic test values — the real ones live in work-PC mappings.)
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


def _profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="inline_id_test",
        profile_version=1,
        created_from=[],
        last_updated="2026-06-30",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
            priority_marker_pattern=r"\(([A-Za-z][A-Za-z /]+)\)",
            title_strip_pattern=r"\s*ID:\s*\S+\s*$",
        ),
        # Pattern scoped to the requirement type only → SEC headings get no req_id.
        requirement_id=RequirementIdPattern(
            pattern=r"GP-REQ-\d+", anchor="leading_text",
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=4.0, font_size_max=5.5),
    )


def _block(idx: int, text: str, *, size: float, bold: bool = True) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=bold),
    )


def _parse(blocks: list[ContentBlock]):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf",
                    mno="MNOC", release="Mar2026", content_blocks=blocks)
    return GenericStructuralParser(_profile()).parse(ir)


def _by_section(tree, num):
    return next(r for r in tree.requirements if r.section_number == num)


def test_requirement_heading_clean_title_priority_and_id():
    tree = _parse([
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 3GPP specification compliance ID: GP-SEC-99", size=7.3),
        _block(2, "4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345", size=5.7),
    ])
    req = _by_section(tree, "4.1.2")
    assert req.req_id == "GP-REQ-12345"                 # extracted from inline trailing id
    assert req.title == "TS 37.865 shall be supported"  # (priority) + ID:<id> peeled
    assert req.priority == "MANDATORY"                  # captured + uppercased
    assert "ID:" not in req.title


def test_structural_subsection_has_no_req_id_but_clean_title():
    tree = _parse([
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 3GPP specification compliance ID: GP-SEC-99", size=7.3),
    ])
    sec = _by_section(tree, "4.1")
    assert sec.req_id == ""                                   # SEC type → not a requirement
    assert sec.title == "3GPP specification compliance"       # trailing id still stripped
    assert "ID:" not in sec.title


def test_top_section_without_id_unchanged():
    tree = _parse([_block(0, "4 Requirements", size=9.8)])
    top = _by_section(tree, "4")
    assert top.req_id == "" and top.title == "Requirements"


def test_title_strip_is_noop_without_pattern():
    # A profile without title_strip_pattern leaves the id text in place
    # (back-compat: existing corpora are unaffected).
    prof = _profile()
    prof.heading_detection.title_strip_pattern = ""
    blocks = [_block(0, "4.1 Foo bar ID: GP-SEC-1", size=7.3)]
    blocks[0].position.index = 0
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="M",
                    release="R", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    assert "ID: GP-SEC-1" in _by_section(tree, "4.1").title


def test_plan_id_falls_back_to_path_plan():
    """When the profile's plan_metadata yields no plan, the tree (and each req)
    take DocumentIR.plan — the plan the pipeline captured from a per-plan input
    directory (mno-c-ingestion)."""
    blocks = [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1.2 TS 1 shall hold (Mandatory) ID: GP-REQ-1", size=5.7),
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", plan="PlanFoo", content_blocks=blocks)
    tree = GenericStructuralParser(_profile()).parse(ir)
    assert tree.plan_id == "PlanFoo"                      # tree-level from path
    req = next(r for r in tree.requirements if r.req_id == "GP-REQ-1")
    assert req.plan_id == "PlanFoo"                       # per-req falls back to tree
