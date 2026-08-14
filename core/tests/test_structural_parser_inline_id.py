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


def test_borderless_table_rows_not_sections_with_font_gate():
    """require_heading_font_for_numbering=True: body-font, non-bold rows that
    start with a row number leak from a borderless table and must NOT become
    sections; bold/larger real headings still do (mno-c-ingestion)."""
    prof = _profile()
    prof.heading_detection.require_heading_font_for_numbering = True
    blocks = [
        _block(0, "4 Requirements", size=9.8, bold=True),        # real heading
        _block(1, "COL0 COL1", size=4.5, bold=True),             # table header (no number)
        _block(2, "1 alpha beta gamma", size=4.5, bold=False),   # table row
        _block(3, "2 delta epsilon zeta", size=4.5, bold=False),  # table row
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    nums = {r.section_number for r in tree.requirements}
    assert "4" in nums                              # real heading kept
    assert "1" not in nums and "2" not in nums      # leaked rows are not sections


def test_body_font_number_row_is_section_without_font_gate():
    """Default (flag off) preserves the legacy advisory-font behavior: a
    body-font number-prefixed block is still classified as a section."""
    prof = _profile()  # require_heading_font_for_numbering defaults False
    blocks = [_block(0, "1 alpha beta gamma", size=4.5, bold=False)]
    blocks[0].position.index = 0
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="M",
                    release="R", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    assert any(r.section_number == "1" for r in tree.requirements)


def test_priority_scoped_to_requirement_headings_when_flag_set():
    """priority_requires_req_id=True: a structural heading (no req_id) whose
    title contains a marker-shaped token is NOT mined for priority — the token
    stays in the title. Requirement headings are still mined (mno-c-ingestion)."""
    prof = _profile()
    prof.heading_detection.priority_requires_req_id = True
    blocks = [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 Handover (see clause 5) ID: GP-SEC-99", size=7.3),
        _block(2, "4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345", size=5.7),
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    sec = _by_section(tree, "4.1")
    assert sec.req_id == "" and sec.priority == ""        # structural → not mined
    assert "(see clause 5)" in sec.title                  # marker-shaped token retained
    assert "ID:" not in sec.title                         # trailing id still stripped
    req = _by_section(tree, "4.1.2")
    assert req.priority == "MANDATORY"                    # requirement → still mined
    assert "(Mandatory)" not in req.title


def test_priority_mined_from_any_heading_when_flag_unset():
    """Default (flag off) preserves the general FR-31 contract: a structural
    heading with a marker-shaped token IS mined."""
    prof = _profile()  # priority_requires_req_id defaults False
    blocks = [_block(0, "4.1 Handover (Mandatory) ID: GP-SEC-99", size=7.3)]
    blocks[0].position.index = 0
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="M",
                    release="R", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    sec = _by_section(tree, "4.1")
    assert sec.priority == "MANDATORY"                    # mined despite no req_id
    assert "(Mandatory)" not in sec.title


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


def _parse_prof(prof, blocks):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", content_blocks=blocks)
    return GenericStructuralParser(prof).parse(ir)


def test_section_id_captured_with_type_discriminator():
    """Broadened pattern captures both section (SEC) and requirement (REQ) ids;
    requirement_type_pattern marks which nodes are actual requirements."""
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"          # capture both
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"  # REQ = requirement
    tree = _parse_prof(prof, [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 3GPP specification compliance ID: GP-SEC-99", size=7.3),
        _block(2, "4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345",
               size=5.7),
    ])
    sec = _by_section(tree, "4.1")
    assert sec.req_id == "GP-SEC-99"          # section id now captured (was empty)
    assert sec.is_requirement is False         # but flagged a structural section
    req = _by_section(tree, "4.1.2")
    assert req.req_id == "GP-REQ-12345"
    assert req.is_requirement is True          # actual requirement
    # parent linking now resolves the section id (bonus)
    assert req.parent_req_id == "GP-SEC-99"


def test_is_requirement_backcompat_without_type_pattern():
    # No requirement_type_pattern → any req_id is a requirement; no req_id → not.
    prof = _profile()  # pattern GP-REQ-\d+, requirement_type_pattern unset
    tree = _parse_prof(prof, [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1.2 Foo shall hold (Mandatory) ID: GP-REQ-1", size=5.7),
    ])
    assert _by_section(tree, "4.1.2").is_requirement is True
    assert _by_section(tree, "4").is_requirement is False   # no req_id


def test_id_label_extraction_ignores_bare_references():
    """id_label_pattern: a heading's own req_id must be behind the 'ID:' label. A
    bare req-id reference (no 'ID:', e.g. a release-notes citation) is NOT captured
    — the fix for reference→duplicate-requirement collisions (mno-c-ingestion)."""
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"
    prof.requirement_id.id_label_pattern = r"ID:\s*(GP-(?:REQ|SEC)-\d+)"
    tree = _parse_prof(prof, [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345",
               size=5.7),
        _block(2, "4.1 Compliance ID: GP-SEC-99", size=7.3),        # labeled section
        _block(3, "4.9 Release note: see GP-REQ-12345 and GP-SEC-99", size=7.3),  # bare
    ])
    req = _by_section(tree, "4.1.2")
    assert req.req_id == "GP-REQ-12345" and req.is_requirement is True   # labeled id
    sec = _by_section(tree, "4.1")
    assert sec.req_id == "GP-SEC-99" and sec.is_requirement is False     # labeled section
    rn = _by_section(tree, "4.9")
    assert rn.req_id == "" and rn.is_requirement is False                # bare refs ignored


def test_small_font_bare_reqid_block_ignored_with_id_label():
    """The standalone small-font req-id-block path (OA trailing-marker) must ALSO
    respect id_label: a bare req-id in a small-font block is a REFERENCE, not a
    definition, so it does NOT become the section's req_id (mno-c-ingestion)."""
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"
    prof.requirement_id.id_label_pattern = r"ID:\s*(GP-(?:REQ|SEC)-\d+)"
    blocks = [
        _block(0, "9 Release notes", size=9.8),
        _block(1, "9.1 Change entry", size=7.3),
        _block(2, "GP-REQ-12345", size=2.5, bold=False),   # bare id, small font → reference
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    entry = _by_section(tree, "9.1")
    assert entry.req_id == "" and entry.is_requirement is False  # bare small-font ref ignored


def test_trailing_id_marker_with_priority_clean_title():
    """MNO-C format '<title> (Priority) ID: <REQ-ID>' (priority before ID:, id last):
    the trailing 'ID: <id>' is stripped for a clean title, priority is mined from
    the '(Priority)' token, and the req_id is captured (mno-c-ingestion)."""
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"
    prof.requirement_id.id_label_pattern = r"ID:\s*(GP-(?:REQ|SEC)-\d+)"
    prof.heading_detection.priority_requires_req_id = True
    tree = _parse_prof(prof, [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1.2 TS 37.865 shall be supported (Mandatory) ID: GP-REQ-12345",
               size=5.7),
        _block(2, "4.1 Compliance ID: GP-SEC-99", size=7.3),   # section, no priority
    ])
    req = _by_section(tree, "4.1.2")
    assert req.req_id == "GP-REQ-12345" and req.is_requirement is True
    assert req.priority == "MANDATORY"                      # mined from the (Priority) token
    assert req.title == "TS 37.865 shall be supported"      # trailing ID: + (priority) both peeled
    assert "ID:" not in req.title and "Mandatory" not in req.title
    sec = _by_section(tree, "4.1")
    assert sec.req_id == "GP-SEC-99" and sec.priority == ""
    assert sec.title == "Compliance"                        # trailing marker stripped, no priority


def test_body_text_bare_reqid_not_scavenged_with_id_label():
    """A section whose heading carries no 'ID:' must NOT scavenge a bare req-id from
    its BODY text (e.g. a change-log line listing changed ids) as its own id — that
    body id is a reference, not the section's own id (mno-c-ingestion)."""
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"
    prof.requirement_id.id_label_pattern = r"ID:\s*(GP-(?:REQ|SEC)-\d+)"
    blocks = [
        _block(0, "9 Change log", size=9.8),
        _block(1, "9.2 Quarterly change entry", size=7.3),
        _block(2, "Added requirements GP-REQ-111, GP-REQ-222", size=5.0, bold=False),
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="f.pdf", source_format="pdf", mno="MNOC",
                    release="Mar2026", content_blocks=blocks)
    tree = GenericStructuralParser(prof).parse(ir)
    entry = _by_section(tree, "9.2")
    assert entry.req_id == "" and entry.is_requirement is False   # bare body id not scavenged


# ---------------------------------------------------------------------------
# bare_small_font_stamp — id-after-body corpora (strand req-recall)
# ---------------------------------------------------------------------------
#
# Opt-in knob: a small-font paragraph that is EXACTLY a solo req_id,
# closing a section that already carries body text and no req_id of its
# own, is that section's id stamp. Default (knob off) keeps the
# unlabeled-id-is-a-reference contract.


def _stamp_profile(*, stamp: bool):
    prof = _profile()
    prof.requirement_id.pattern = r"GP-(?:REQ|SEC)-\d+"
    prof.requirement_id.requirement_type_pattern = r"GP-REQ-\d+"
    prof.requirement_id.id_label_pattern = r"ID:\s*(GP-(?:REQ|SEC)-\d+)"
    prof.requirement_id.bare_small_font_stamp = stamp
    return prof


def test_bare_stamp_assigns_section_id_when_enabled():
    tree = _parse_prof(_stamp_profile(stamp=True), [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 Device behavior", size=7.3),
        _block(2, "The device shall do X.", size=5.0, bold=False),
        _block(3, "GP-REQ-200", size=2.5, bold=False),   # id-after-body stamp
    ])
    sec = _by_section(tree, "4.1")
    assert sec.req_id == "GP-REQ-200" and sec.is_requirement is True
    assert "shall do X" in sec.text


def test_bare_stamp_needs_preceding_body():
    # Release-notes shape: bare id directly after the heading, no body —
    # stays a reference even with the knob on.
    tree = _parse_prof(_stamp_profile(stamp=True), [
        _block(0, "9 Release notes", size=9.8),
        _block(1, "9.1 Change entry", size=7.3),
        _block(2, "GP-REQ-201", size=2.5, bold=False),
    ])
    assert _by_section(tree, "9.1").req_id == ""


def test_bare_stamp_ignored_when_section_has_id():
    tree = _parse_prof(_stamp_profile(stamp=True), [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.2 Behavior ID: GP-REQ-202", size=7.3),
        _block(2, "Body text.", size=5.0, bold=False),
        _block(3, "GP-REQ-203", size=2.5, bold=False),   # stray bare id
    ])
    sec = _by_section(tree, "4.2")
    assert sec.req_id == "GP-REQ-202"
    assert all(r.req_id != "GP-REQ-203" for r in tree.requirements)


def test_bare_stamp_off_by_default_keeps_reference_contract():
    tree = _parse_prof(_stamp_profile(stamp=False), [
        _block(0, "4 Requirements", size=9.8),
        _block(1, "4.1 Device behavior", size=7.3),
        _block(2, "The device shall do X.", size=5.0, bold=False),
        _block(3, "GP-REQ-204", size=2.5, bold=False),
    ])
    assert _by_section(tree, "4.1").req_id == ""
