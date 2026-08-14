"""Announced-requirement path (strand req-recall).

In id_label corpora a standalone body paragraph that is nothing but the
labeled id — optionally behind a parenthesized marker — announces a
requirement whose statement follows in the NEXT block(s):

    (Mandatory) ID: GP-REQ-100
    The device shall do X.

Previously the announcement was absorbed into the preceding node (or, at
best, scavenged as the enclosing section's id), so every announcement
after a section's first vanished from the corpus. Now each spawns its own
Requirement — structurally like a leading-id / table-anchored node (no
own section_number, parented to the enclosing heading) — and subsequent
body/table blocks attach to it until the next heading or announcement.

Fixtures mirror the layout-provider shape: headings carry FontInfo,
announcement/body paragraphs do not (the no-font-info absorb gate is
exactly where the old absorption happened).
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
        profile_name="announced_id_test",
        profile_version=1,
        created_from=[],
        last_updated="2026-08-14",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
            priority_marker_pattern=r"\(([A-Za-z][A-Za-z /]+)\)",
            title_strip_pattern=r"\s*ID:\s*\S+\s*$",
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"GP-(?:REQ|SEC)-\d+",
            requirement_type_pattern=r"GP-REQ-\d+",
            id_label_pattern=r"ID:\s*(GP-(?:REQ|SEC)-\d+)",
            anchor="leading_text",
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=4.0, font_size_max=5.5),
    )


def _heading(idx: int, text: str, *, size: float = 7.3) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=True),
    )


def _para(idx: int, text: str) -> ContentBlock:
    """Layout-provider body paragraph — no FontInfo."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
    )


def _table(idx: int, html: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=1, index=idx),
        html=html,
    )


def _parse(blocks: list[ContentBlock]):
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf",
                    mno="MNOC", release="Mar2026", content_blocks=blocks)
    return GenericStructuralParser(_profile()).parse(ir)


def _by_id(tree, rid):
    return next((r for r in tree.requirements if r.req_id == rid), None)


def test_announcement_spawns_requirement_with_following_body():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "(Mandatory) ID: GP-REQ-100"),
        _para(3, "The device shall do X."),
        _para(4, "(Optional) ID: GP-REQ-101"),
        _para(5, "It should also do Y."),
    ])
    r100 = _by_id(tree, "GP-REQ-100")
    r101 = _by_id(tree, "GP-REQ-101")
    assert r100 is not None and r101 is not None
    assert r100.is_requirement and r101.is_requirement
    assert "shall do X" in r100.text and "also do Y" not in r100.text
    assert "also do Y" in r101.text
    assert r100.priority == "MANDATORY" and r101.priority == "OPTIONAL"
    assert r100.section_number == "" and r100.parent_section == "4.1"
    sec = _by_id(tree, "GP-SEC-99")
    assert "shall do X" not in sec.text        # body not absorbed into section
    assert "GP-REQ-100" in sec.children and "GP-REQ-101" in sec.children


def test_labeled_id_in_prose_is_not_an_announcement():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "As stated in ID: GP-REQ-102, the behavior is unchanged."),
    ])
    assert _by_id(tree, "GP-REQ-102") is None
    sec = _by_id(tree, "GP-SEC-99")
    assert "behavior is unchanged" in sec.text  # absorbed as ordinary body


def test_bare_solo_id_paragraph_is_not_an_announcement():
    # Prior contract: under id_label corpora an UNLABELED id is a reference
    # (see test_small_font_bare_reqid_block_ignored_with_id_label).
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "GP-REQ-103"),
    ])
    assert _by_id(tree, "GP-REQ-103") is None


def test_section_type_id_announcement_does_not_spawn():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "(Info) ID: GP-SEC-55"),
        _para(3, "Structural note."),
    ])
    assert _by_id(tree, "GP-SEC-55") is None
    sec = _by_id(tree, "GP-SEC-99")
    assert "Structural note" in sec.text


def test_table_attaches_to_announced_requirement():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "(Mandatory) ID: GP-REQ-104"),
        _para(3, "Support the following bands:"),
        _table(4, "<table><tr><td>band foo</td></tr></table>"),
    ])
    req = _by_id(tree, "GP-REQ-104")
    assert req is not None
    assert len(req.tables) == 1 and "band foo" in req.text
    sec = _by_id(tree, "GP-SEC-99")
    assert not sec.tables


def test_new_heading_resets_announced_cursor():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "(Mandatory) ID: GP-REQ-105"),
        _para(3, "Requirement body."),
        _heading(4, "4.2 Next area ID: GP-SEC-98"),
        _para(5, "Preamble of the next section."),
    ])
    req = _by_id(tree, "GP-REQ-105")
    assert "Preamble" not in req.text
    nxt = _by_id(tree, "GP-SEC-98")
    assert "Preamble" in nxt.text


def test_announcement_before_any_heading_still_spawns():
    tree = _parse([
        _para(0, "(Mandatory) ID: GP-REQ-106"),
        _para(1, "Orphan requirement body."),
    ])
    req = _by_id(tree, "GP-REQ-106")
    assert req is not None
    assert "Orphan requirement body" in req.text
    assert req.parent_section == "" and req.parent_req_id == ""
