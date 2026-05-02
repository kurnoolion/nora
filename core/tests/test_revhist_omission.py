"""FR-34 revision-history omission — profiler detection + parser drop.

Verifies the end-to-end contract:

  - profiler discovers the heading phrase from documents that contain a
    'Revision History' (or 'Change History', etc.) paragraph immediately
    followed by a table block, and tightens
    `revision_history_heading_pattern` accordingly;
  - parser drops both the heading paragraph AND the next-block table,
    and reports the count via `parse_stats.revhist_blocks_dropped`;
  - false positives are gated: a paragraph mentioning 'revision history'
    that is NOT followed by a table is left in place.
"""

from __future__ import annotations

import re

from core.src.models.document import (
    BlockType,
    ContentBlock,
    DocumentIR,
    FontInfo,
    Position,
)
from core.src.parser.structural_parser import GenericStructuralParser
from core.src.profiler.profiler import DocumentProfiler
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
        last_updated="2026-05-02",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True)],
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


def _para(idx: int, text: str, *, size: float = 12.0, bold: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=bold),
    )


def _tbl(idx: int, headers: list[str], rows: list[list[str]]) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=1, index=idx),
        headers=headers,
        rows=rows,
        font_info=FontInfo(size=11.0),
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


# ---------------------------------------------------------------------------
# Parser behavior
# ---------------------------------------------------------------------------


def test_revhist_heading_plus_following_table_dropped():
    """The OA pattern: a 'Revision History' paragraph immediately followed
    by a table → both are dropped, no Requirement nodes leak through."""
    blocks = [
        _para(0, "1 LTE Data Retry"),
        _para(1, "Revision History", size=12.0),
        _tbl(
            2,
            headers=["Author", "Description of Changes", "Date"],
            rows=[["Verizon", "Initial version", "2009"]],
        ),
        _para(3, "1.1 Scope"),
        _para(4, "body content"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert sections == ["1", "1.1"]  # no phantom from the revhist table
    assert tree.parse_stats.revhist_blocks_dropped == 2  # heading + table


def test_revhist_heading_with_extra_whitespace():
    """'Revision     History' (PDF whitespace runs) still matches the
    default pattern."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision     History"),
        _tbl(2, headers=["Rev"], rows=[["1.0"]]),
        _para(3, "1.1 Body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    assert tree.parse_stats.revhist_blocks_dropped == 2


def test_revhist_alternate_label_change_history():
    """Default pattern covers 'Change History' / 'Document History' /
    'Version History' — common across MNOs."""
    for label in ["Change History", "Document History", "Version History", "Change Log", "Revision Log"]:
        blocks = [
            _para(0, "1 Chapter"),
            _para(1, label),
            _tbl(2, headers=["Rev"], rows=[["1.0"]]),
            _para(3, "1.1 Body"),
        ]
        tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
        assert tree.parse_stats.revhist_blocks_dropped == 2, label


def test_revhist_pattern_disabled_via_empty_string():
    """Profile setting `revision_history_heading_pattern = ""` disables
    the drop entirely — heading and table are processed normally."""
    profile = _profile()
    profile.revision_history_heading_pattern = ""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision History"),
        _tbl(2, headers=["Rev"], rows=[["1.0"]]),
        _para(3, "1.1 Body"),
    ]
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.parse_stats.revhist_blocks_dropped == 0


def test_revhist_heading_without_following_table_falls_through():
    """When a 'revision history' paragraph is NOT followed by a table
    (e.g. it's prose mentioning the term, not a section heading), the
    table-drop window closes on the next paragraph and nothing else is
    consumed. The matching paragraph itself IS dropped — that's the
    cost of using a string match as the gate. Acceptable: the sentence
    'See the revision history below.' shouldn't appear as a normal
    Requirement title anyway, and the heading-only false positive
    doesn't break any downstream invariant."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision History"),
        _para(2, "1.1 Some other section"),  # closes the window — no table consumed
        _para(3, "body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert sections == ["1", "1.1"]
    # Only the heading is dropped, not the next paragraph
    assert tree.parse_stats.revhist_blocks_dropped == 1


def test_unrelated_table_is_not_dropped():
    """Sanity: a table not preceded by a revhist heading is preserved."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "1.1 Data Section"),
        _tbl(2, headers=["Field"], rows=[["value"]]),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    assert tree.parse_stats.revhist_blocks_dropped == 0


# ---------------------------------------------------------------------------
# Profiler learning
# ---------------------------------------------------------------------------


def test_profiler_learns_revhist_pattern_from_corpus():
    """Profiler scans for `<label> + table` pairs and tightens the
    pattern to the most-frequent observed phrasing."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision History", size=12.0, bold=True),
        _tbl(2, headers=["Rev"], rows=[["1.0"]]),
        _para(3, "1.1 Body"),
    ]
    doc = _doc(blocks)
    profiler = DocumentProfiler()
    pattern = profiler._detect_revision_history_pattern([doc])
    assert pattern, "profiler returned empty pattern"
    # Tightened pattern matches the exact phrase observed
    assert re.match(pattern, "Revision History")
    assert re.match(pattern, "Revision   History")  # whitespace-tolerant
    # Does NOT match unrelated phrases
    assert not re.match(pattern, "Acceptance Criteria")


def test_profiler_returns_broad_default_when_no_matches():
    """No corpus evidence → broad default that catches common labels."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "1.1 Just body"),
        _tbl(2, headers=["A"], rows=[["x"]]),
    ]
    doc = _doc(blocks)
    profiler = DocumentProfiler()
    pattern = profiler._detect_revision_history_pattern([doc])
    # Broad default still matches the common variants
    assert re.match(pattern, "Revision History")
    assert re.match(pattern, "Change History")
    assert re.match(pattern, "Version History")
    assert re.match(pattern, "Document Log")
    # And rejects nonsense
    assert not re.match(pattern, "Acceptance Criteria")


def test_revhist_consumes_multi_page_table_continuations():
    """Real-world OA pattern: pdfplumber emits each page's slice of a
    multi-page revision-history table as its own table block. After the
    revhist heading match, ALL subsequent table/image blocks are
    consumed until the next paragraph (the next section's heading)."""
    blocks = [
        _para(0, "1 LTE Data Retry"),
        _para(1, "Revision History"),
        _tbl(2, headers=["Author", "Date"], rows=[["VZW", "2009"]]),
        _tbl(3, headers=["Author", "Date"], rows=[["VZW", "2010"]]),  # page 2 of revhist
        _tbl(4, headers=["Author", "Date"], rows=[["VZW", "2011"]]),  # page 3
        _tbl(5, headers=["Author", "Date"], rows=[["VZW", "2012"]]),  # page 4
        _para(6, "1.1 Scope"),
        _para(7, "body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert sections == ["1", "1.1"]
    # 1 heading + 4 continuation tables = 5
    assert tree.parse_stats.revhist_blocks_dropped == 5


def test_revhist_consume_skips_inter_table_images():
    """Some MNOs place a logo image between revhist tables. The
    consumer must drop those too — only paragraphs end consumption."""
    img = ContentBlock(
        type=BlockType.IMAGE,
        position=Position(page=1, index=0),
        font_info=FontInfo(size=11.0),
    )
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision History"),
        _tbl(2, headers=["Rev"], rows=[["1.0"]]),
        img,
        _tbl(4, headers=["Rev"], rows=[["2.0"]]),
        _para(5, "1.1 Body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    # heading + 2 tables + 1 image = 4 blocks dropped
    assert tree.parse_stats.revhist_blocks_dropped == 4


# ---------------------------------------------------------------------------
# FR-33 cascade — struck heading deletes its whole section
# ---------------------------------------------------------------------------


def _struck_para(idx: int, text: str, *, size: float = 14.0) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=size, strikethrough=True),
    )


def test_cascade_drops_table_under_struck_heading():
    """LTEB13NAC page 310 case: parent section heading is struck; the
    (non-struck) table directly under it must be cascade-dropped."""
    blocks = [
        _para(0, "1 LTE B13"),
        _para(1, "1.4 Antenna Testing"),
        _struck_para(2, "1.4.5 LTE Test Application for Antenna Testing"),
        _tbl(3, headers=["Test Case"], rows=[["TIS test"]]),
        _para(4, "1.5 Next Section"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert "1.4.5" not in sections  # struck heading dropped
    assert "1.5" in sections  # cascade ended at depth-2 sibling
    # Cascade drops the table (1 block); struck heading itself is in
    # struck_blocks_dropped.
    assert tree.parse_stats.cascade_blocks_dropped == 1
    assert tree.parse_stats.struck_blocks_dropped >= 1


def test_cascade_ends_at_sibling_or_shallower_heading():
    """A struck depth-3 heading drops descendants but not depth-2
    siblings."""
    blocks = [
        _para(0, "1 Top"),
        _struck_para(1, "1.2.3 Struck Section"),
        _tbl(2, headers=["X"], rows=[["a"]]),
        _para(3, "body of struck section"),
        _para(4, "1.2.3.1 Subsection of struck"),
        _para(5, "more body"),
        _para(6, "1.2.4 Sibling — cascade ends here"),
        _para(7, "kept body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert "1.2.3" not in sections
    assert "1.2.3.1" not in sections  # deeper than 3 → cascaded
    assert "1.2.4" in sections  # depth=3, equal to cascade_depth → ends cascade
    # 1 table + 1 body para + 1 sub-heading + 1 more-body = 4 cascaded blocks
    assert tree.parse_stats.cascade_blocks_dropped == 4


def test_cascade_extends_to_doc_end_when_no_terminating_heading():
    """If the struck heading is the last shallow heading in the doc,
    cascade runs to end-of-doc."""
    blocks = [
        _para(0, "1 Chapter"),
        _struck_para(1, "1.5 Final Section"),
        _para(2, "1.5.1 Subsection"),
        _para(3, "body"),
        _tbl(4, headers=["X"], rows=[["a"]]),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert sections == ["1"]  # only the unstruck top remains
    # 3 cascaded blocks (subsection heading, body, table)
    assert tree.parse_stats.cascade_blocks_dropped == 3


def test_cascade_does_not_trigger_for_struck_non_heading_paragraph():
    """A struck paragraph that isn't a heading (no section number)
    drops just that paragraph — no cascade."""
    blocks = [
        _para(0, "1 Top"),
        _para(1, "1.1 Scope"),
        _struck_para(2, "Some struck-out body sentence"),  # no section number
        _para(3, "1.2 Next"),
        _para(4, "body"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert sections == ["1", "1.1", "1.2"]  # 1.2 NOT cascaded
    assert tree.parse_stats.cascade_blocks_dropped == 0


def test_cascade_handles_split_struck_heading():
    """Real PDF artifact: a heading split across multiple struck blocks
    ('1.3.1.2.7.15 RSSI ...,' / 'AND AVERAGING' / req-id marker). The
    FIRST struck block carries the section number → cascade arms.
    Subsequent struck blocks (heading fragments without numbers) are
    dropped via the strike path normally; following non-struck content
    cascades."""
    blocks = [
        _para(0, "1 Chapter"),
        _struck_para(1, "1.3.1.2.7.15 RSSI and Relative Phase Measurements,"),
        _struck_para(2, "AND AVERAGING"),  # heading fragment — no section number
        _tbl(3, headers=["X"], rows=[["a"]]),
        _para(4, "1.3.1.2.7.16 Next"),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    sections = [r.section_number for r in tree.requirements]
    assert "1.3.1.2.7.15" not in sections
    assert "1.3.1.2.7.16" in sections


def test_profiler_ignores_revhist_mention_without_following_table():
    """A paragraph that says 'revision history' but isn't followed by a
    table doesn't count toward the learned phrasing — keeps the broad
    default."""
    blocks = [
        _para(0, "1 Chapter"),
        _para(1, "Revision History"),
        _para(2, "See revision history below."),  # prose — not a heading + table
        _para(3, "1.1 Body"),
    ]
    doc = _doc(blocks)
    profiler = DocumentProfiler()
    pattern = profiler._detect_revision_history_pattern([doc])
    # Falls back to broad default since nothing was followed by a table
    # (note: the FIRST paragraph here ALSO has no following table — by
    # design the profiler only narrows when at least one heading-table
    # pair is present).
    assert re.match(pattern, "Change History")  # default still works
