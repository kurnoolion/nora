"""Tests for ParseLog generation: dropped block recording, range merging,
glossary section tracking, and acronym source tagging.

Uses hand-crafted in-memory fixtures — the same pattern as the other
structural-parser test files.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _profile(
    *,
    ignore_strikeout: bool = True,
    toc_pattern: str = r".*\.{3,}\s*\d+\s*$",
    toc_threshold: float = 0.8,
    revhist_pattern: str = r"(?i)^\s*(revision|change|version)\s+(history|log)\s*$",
    defs_section_pattern: str = r"(?i)acronym|definition|glossary",
    defs_entry_pattern: str = r"^([A-Z][A-Z0-9/-]{1,15})\s*[—–:\-]\s*(.+?)$",
) -> DocumentProfile:
    return DocumentProfile(
        profile_name="test-log",
        profile_version=1,
        created_from=[],
        last_updated="2026-05-04",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True)],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
            definitions_section_pattern=defs_section_pattern,
        ),
        requirement_id=RequirementIdPattern(),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
        ignore_strikeout=ignore_strikeout,
        toc_detection_pattern=toc_pattern,
        toc_page_threshold=toc_threshold,
        revision_history_heading_pattern=revhist_pattern,
        definitions_entry_pattern=defs_entry_pattern,
    )


def _para(idx: int, text: str, page: int = 1, *, size: float = 12.0,
          struck: bool = False) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=size, strikethrough=struck),
    )


def _heading(idx: int, text: str, page: int = 1, size: float = 14.0) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=size, bold=True),
    )


def _table(idx: int, page: int = 1,
           headers: list[str] | None = None,
           rows: list[list[str]] | None = None) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=page, index=idx),
        headers=headers or [],
        rows=rows or [],
    )


def _doc(blocks: list[ContentBlock], source: str = "fixture.pdf") -> DocumentIR:
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(
        source_file=source,
        source_format="pdf",
        mno="VZW",
        release="OA-test",
        doc_type="requirement",
        content_blocks=blocks,
    )


def _parse(blocks, **profile_kwargs):
    return GenericStructuralParser(_profile(**profile_kwargs)).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# Struck-block recording
# ---------------------------------------------------------------------------

def test_struck_block_recorded():
    """A struck paragraph block appears in dropped_blocks with reason text_strikethrough."""
    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "body text"),
        _para(2, "deleted content", struck=True),
        _para(3, "more body"),
    ])
    log = tree.parse_log
    assert log is not None
    reasons = [r.reason for r in log.dropped_blocks]
    assert "text_strikethrough" in reasons
    struck = [r for r in log.dropped_blocks if r.reason == "text_strikethrough"]
    assert len(struck) == 1
    assert struck[0].block_start == 2
    assert struck[0].block_end == 2
    assert struck[0].block_count == 1
    assert struck[0].page_start == 1


def test_consecutive_struck_blocks_merged():
    """Three consecutive struck blocks become one range."""
    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "body"),
        _para(2, "struck A", struck=True),
        _para(3, "struck B", struck=True),
        _para(4, "struck C", struck=True),
        _para(5, "body after"),
    ])
    log = tree.parse_log
    struck = [r for r in log.dropped_blocks if r.reason == "text_strikethrough"]
    assert len(struck) == 1
    assert struck[0].block_start == 2
    assert struck[0].block_end == 4
    assert struck[0].block_count == 3


def test_non_consecutive_struck_blocks_are_separate_ranges():
    """Struck blocks with a non-struck block between them form two ranges."""
    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "struck first", struck=True),
        _para(2, "normal in between"),
        _para(3, "struck second", struck=True),
    ])
    log = tree.parse_log
    struck = [r for r in log.dropped_blocks if r.reason == "text_strikethrough"]
    assert len(struck) == 2
    assert struck[0].block_start == 1 and struck[0].block_end == 1
    assert struck[1].block_start == 3 and struck[1].block_end == 3


def test_ignore_strikeout_false_records_nothing():
    """When ignore_strikeout=False, struck blocks are processed normally and not logged."""
    tree = _parse(
        [
            _heading(0, "1 Introduction"),
            _para(1, "struck but kept", struck=True),
        ],
        ignore_strikeout=False,
    )
    log = tree.parse_log
    struck = [r for r in log.dropped_blocks if r.reason == "text_strikethrough"]
    assert struck == []


# ---------------------------------------------------------------------------
# Cascade recording
# ---------------------------------------------------------------------------

def test_cascade_blocks_recorded():
    """Blocks under a struck heading are recorded with reason cascade."""
    blocks = [
        # depth-1 heading
        _heading(0, "1 Normal Section"),
        _para(1, "normal body"),
        # depth-2 struck heading — arms cascade
        ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=2),
            text="1.1 Deleted Section",
            font_info=FontInfo(size=14.0, bold=True, strikethrough=True),
        ),
        # These should be cascade-dropped
        _para(3, "cascade body 1"),
        _para(4, "cascade body 2"),
        # depth-2 heading stops cascade
        _heading(5, "1.2 Kept Section"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log

    struck = [r for r in log.dropped_blocks if r.reason == "text_strikethrough"]
    cascade = [r for r in log.dropped_blocks if r.reason == "cascade"]
    assert len(struck) == 1 and struck[0].block_start == 2
    assert len(cascade) == 1
    assert cascade[0].block_start == 3
    assert cascade[0].block_end == 4
    assert cascade[0].block_count == 2


# ---------------------------------------------------------------------------
# TOC recording
# ---------------------------------------------------------------------------

def test_toc_pattern_match_recorded():
    """A block matching the TOC entry pattern is logged as reason=toc."""
    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "1.1 Procedures................. 5"),
        _para(2, "normal body text no dots"),
    ])
    log = tree.parse_log
    toc_entries = [r for r in log.dropped_blocks if r.reason == "toc"]
    assert len(toc_entries) == 1
    assert toc_entries[0].block_start == 1


def test_toc_quick_access_populated():
    """parse_log.toc quick-access is set when at least one toc block is dropped."""
    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "Chapter 1.......... 2"),
        _para(2, "Chapter 2.......... 8"),
        _para(3, "real body"),
    ])
    log = tree.parse_log
    assert log.toc is not None
    assert log.toc.block_start == 1
    assert log.toc.block_end == 2


def test_no_toc_quick_access_when_none_dropped():
    """parse_log.toc is None when no TOC blocks are dropped."""
    tree = _parse(
        [
            _heading(0, "1 Introduction"),
            _para(1, "regular body"),
        ],
        toc_pattern="",
    )
    log = tree.parse_log
    assert log.toc is None


# ---------------------------------------------------------------------------
# Revision-history recording
# ---------------------------------------------------------------------------

def test_revhist_heading_and_table_recorded():
    """Revision-history heading + following table blocks are logged as revhist."""
    blocks = [
        _heading(0, "1 Introduction"),
        _para(1, "body"),
        _para(2, "Revision History"),
        _table(3, rows=[["v1.0", "Initial"]]),
        _para(4, "next section heading material"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    revhist = [r for r in log.dropped_blocks if r.reason == "revhist"]
    # heading (idx 2) and table (idx 3) both dropped
    assert len(revhist) >= 1
    all_revhist_indices = set()
    for r in revhist:
        all_revhist_indices.update(range(r.block_start, r.block_end + 1))
    assert 2 in all_revhist_indices  # heading dropped
    assert 3 in all_revhist_indices  # table dropped


def test_revhist_quick_access_populated():
    """parse_log.revision_history is set when revhist blocks are dropped."""
    blocks = [
        _heading(0, "1 Introduction"),
        _para(1, "Revision History"),
        _table(2, rows=[["v1", "first"]]),
        _heading(3, "2 Requirements"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    assert log.revision_history is not None
    assert log.revision_history.block_start == 1


# ---------------------------------------------------------------------------
# Glossary section tracking
# ---------------------------------------------------------------------------

def test_glossary_section_tracked():
    """Glossary section location is captured in parse_log.glossary_section."""
    blocks = [
        _heading(0, "1 Requirements", page=1),
        _para(1, "req body", page=1),
        _heading(2, "2 Definitions", page=3),
        _table(3, page=3,
               headers=["Acronym", "Meaning"],
               rows=[["SDM", "Subscription Data Management"]]),
        _heading(4, "3 Annex", page=4),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    assert log.glossary_section is not None
    assert log.glossary_section.section_number == "2"
    assert "Definitions" in log.glossary_section.section_title
    assert log.glossary_section.page_start == 3
    assert log.glossary_section.acronym_count == 1


def test_glossary_section_none_when_no_defs_section():
    """glossary_section is None when no section matches the definitions pattern."""
    tree = _parse(
        [
            _heading(0, "1 Requirements"),
            _para(1, "body"),
        ],
        defs_section_pattern="",
    )
    log = tree.parse_log
    assert log.glossary_section is None


# ---------------------------------------------------------------------------
# Acronym source tagging
# ---------------------------------------------------------------------------

def test_acronyms_table_source_tagged():
    """Acronyms extracted from tables have source='table'."""
    blocks = [
        _heading(0, "1 Requirements"),
        _para(1, "body"),
        _heading(2, "2 Acronyms"),
        _table(3,
               headers=["Term", "Meaning"],
               rows=[
                   ["SDM", "Subscription Data Management"],
                   ["IMS", "IP Multimedia Subsystem"],
               ]),
        _heading(4, "3 Annex"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    assert len(log.acronyms) == 2
    assert all(a.source == "table" for a in log.acronyms)
    acronym_map = {a.acronym: a.expansion for a in log.acronyms}
    assert acronym_map["SDM"] == "Subscription Data Management"
    assert acronym_map["IMS"] == "IP Multimedia Subsystem"


def test_acronyms_body_text_source_tagged():
    """Acronyms extracted from body text have source='body_text'."""
    blocks = [
        _heading(0, "1 Requirements"),
        _para(1, "body"),
        _heading(2, "2 Definitions"),
        _para(3, "SDM — Subscription Data Management\nIMS — IP Multimedia Subsystem"),
        _heading(4, "3 Annex"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    body_acronyms = [a for a in log.acronyms if a.source == "body_text"]
    assert len(body_acronyms) == 2
    names = {a.acronym for a in body_acronyms}
    assert "SDM" in names and "IMS" in names


def test_duplicate_acronym_first_wins_in_source_order():
    """When body_text and table both have the same term, body_text entry is kept."""
    blocks = [
        _heading(0, "1 Requirements"),
        _heading(1, "2 Definitions"),
        # body text comes first → body_text source
        _para(2, "SDM — Subscription Data Management"),
        # table second → duplicate, should be skipped
        _table(3,
               headers=["Acronym", "Meaning"],
               rows=[["SDM", "Different Meaning"]]),
        _heading(4, "3 Annex"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    sdm_entries = [a for a in log.acronyms if a.acronym == "SDM"]
    assert len(sdm_entries) == 1
    assert sdm_entries[0].source == "body_text"
    assert sdm_entries[0].expansion == "Subscription Data Management"


# ---------------------------------------------------------------------------
# Summary counts
# ---------------------------------------------------------------------------

def test_summary_counts_match_dropped_blocks():
    """Summary counters match the actual dropped block entries."""
    blocks = [
        _heading(0, "1 Requirements"),
        _para(1, "TOC entry ........ 3"),
        _para(2, "struck content", struck=True),
        _para(3, "struck content 2", struck=True),
        _para(4, "Revision History"),
        _table(5, rows=[["v1", "init"]]),
        _para(6, "body"),
    ]
    tree = _parse(blocks)
    log = tree.parse_log
    s = log.summary
    assert s.toc_blocks_dropped == 1
    assert s.struck_blocks_dropped == 2
    assert s.revhist_blocks_dropped == 2  # heading + table
    assert s.cascade_blocks_dropped == 0
    assert s.total_dropped == 1 + 2 + 2  # toc + struck + revhist


def test_summary_glossary_count():
    """summary.glossary_acronyms reflects the number of extracted acronyms."""
    blocks = [
        _heading(0, "1 Requirements"),
        _heading(1, "2 Acronyms"),
        _table(2,
               headers=["Acronym", "Meaning"],
               rows=[["A", "Alpha"], ["B", "Beta"], ["C", "Charlie"]]),
        _heading(3, "3 Annex"),
    ]
    tree = _parse(blocks)
    assert tree.parse_log.summary.glossary_acronyms == 3


# ---------------------------------------------------------------------------
# ParseLog is not embedded in tree JSON
# ---------------------------------------------------------------------------

def test_parse_log_excluded_from_tree_json(tmp_path):
    """parse_log is not serialized into the RequirementTree JSON file."""
    import json

    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "body"),
    ])
    out = tmp_path / "tree.json"
    tree.save_json(out)
    data = json.loads(out.read_text())
    assert "parse_log" not in data


def test_parse_log_survives_round_trip(tmp_path):
    """parse_log is generated fresh on each parse(); loading a tree.json preserves
    the tree but parse_log is None (not stored in JSON)."""
    from core.src.parser.structural_parser import RequirementTree

    tree = _parse([
        _heading(0, "1 Introduction"),
        _para(1, "body"),
        _para(2, "struck", struck=True),
    ])
    assert tree.parse_log is not None

    out = tmp_path / "tree.json"
    tree.save_json(out)
    loaded = RequirementTree.load_json(out)
    assert loaded.parse_log is None  # not embedded; caller re-runs parse to get it
