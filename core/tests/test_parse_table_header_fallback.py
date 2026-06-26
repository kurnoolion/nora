"""Tests for the table-header fallback paths:

- ``revhist_table_header_pattern``: drops a TABLE block when its joined
  column headers match the pattern AND there's no preceding label
  paragraph. Continuation tables also drop until the next paragraph
  (same consume semantics as the label path).
- ``heading_detection.definitions_table_header_pattern``: when no
  section title matches ``definitions_section_pattern``, the parser
  walks every section's tables and uses the first whose joined headers
  match. The matched table feeds ``definitions_map`` extraction.
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


def _profile(
    *,
    revhist_label_pattern: str = "",
    revhist_table_header_pattern: str = "",
    definitions_section_pattern: str = "",
    definitions_table_header_pattern: str = "",
) -> DocumentProfile:
    return DocumentProfile(
        profile_name="test-table-header",
        heading_detection=HeadingDetection(
            method="numbering",
            levels=[HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True)],
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            definitions_section_pattern=definitions_section_pattern,
            definitions_table_header_pattern=definitions_table_header_pattern,
        ),
        requirement_id=RequirementIdPattern(),
        plan_metadata=PlanMetadata(),
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
        revision_history_label_pattern=revhist_label_pattern,
        revhist_table_header_pattern=revhist_table_header_pattern,
    )


def _para(idx: int, text: str, page: int = 1) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=12.0),
    )


def _heading(idx: int, text: str, page: int = 1) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
    )


def _table(idx: int, headers: list[str], rows: list[list[str]],
           page: int = 1) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=page, index=idx),
        headers=headers,
        rows=rows,
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(source_file="fixture.docx", source_format="docx",
                      content_blocks=blocks)


# ---------------------------------------------------------------------------
# revhist_table_header_pattern
# ---------------------------------------------------------------------------

def test_table_header_fallback_drops_bare_revhist_table():
    """Bare revhist table at the top of the doc — no introducing heading —
    gets dropped by the table-header fallback."""
    blocks = [
        _table(0, headers=["Rev.", "Author", "Description of Changes", "Date"],
               rows=[["1.0", "x", "initial", "2026-01-01"]]),
        _heading(1, "1 Introduction"),
        _para(2, "Body text."),
    ]
    profile = _profile(
        revhist_table_header_pattern=r"(?i)rev\.?\s*\|\s*author",
    )
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    # The revhist table is gone; the introduction survives.
    assert tree.parse_stats.revhist_blocks_dropped == 1
    titles = [r.title for r in tree.requirements]
    assert "Introduction" in titles


def test_table_header_fallback_consumes_continuation_tables():
    """When a revhist table spans pages, pdfplumber slices it into multiple
    table blocks. The fallback must arm the same consume state the label
    path uses so subsequent tables also drop."""
    blocks = [
        _table(0, headers=["Rev.", "Author", "Date"],
               rows=[["1.0", "x", "2026-01-01"]]),
        # Continuation slice on page 2 — no headers, just a body table.
        _table(1, headers=[], rows=[["1.1", "y", "2026-02-01"]], page=2),
        _heading(2, "1 Introduction", page=2),
    ]
    profile = _profile(
        revhist_table_header_pattern=r"(?i)rev\.?\s*\|\s*author",
    )
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.parse_stats.revhist_blocks_dropped == 2


def test_table_header_fallback_disabled_when_pattern_empty():
    """Empty `revhist_table_header_pattern` ⇒ no table-shape detection.
    The bare revhist table reaches the tree as ordinary content."""
    blocks = [
        _table(0, headers=["Rev.", "Author", "Date"],
               rows=[["1.0", "x", "2026-01-01"]]),
        _heading(1, "1 Introduction"),
    ]
    profile = _profile(revhist_table_header_pattern="")  # default
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert tree.parse_stats.revhist_blocks_dropped == 0


# ---------------------------------------------------------------------------
# definitions_table_header_pattern
# ---------------------------------------------------------------------------

def test_definitions_table_header_fallback_extracts_when_no_section_title():
    """No section heading mentions 'glossary' or 'acronym', but a table
    has the canonical Acronym | Definition header signature. The
    fallback should still extract the entries."""
    blocks = [
        _heading(0, "1 Introduction"),
        _para(1, "Setup paragraph."),
        _table(
            2,
            headers=["Acronym", "Definition"],
            rows=[
                ["GPP", "Global Pretend Protocol"],
                ["IMS", "IP Multimedia Subsystem"],
            ],
        ),
    ]
    profile = _profile(
        definitions_section_pattern=r"(?i)acronym|definition|glossary",
        definitions_table_header_pattern=r"(?i)acronym\s*\|\s*definition",
    )
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    # Two entries extracted via the table-header fallback path.
    assert "GPP" in tree.definitions_map
    assert tree.definitions_map["IMS"] == "IP Multimedia Subsystem"


def test_word_toc_style_paragraphs_skipped_before_glossary_match():
    """A paragraph with Word's canonical ``toc N`` style is structurally
    a TOC entry. Glossary section-title matching (and any other
    text-based matcher) must not see it — otherwise the TOC's
    "Glossary ........ 5" line gets mis-classified as the real
    Glossary section heading."""
    blocks = [
        # TOC entry naming Glossary — must be dropped pre-classification.
        ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=0),
            text="Glossary ........ 5",
            style="toc 1",
            font_info=FontInfo(size=11.0),
        ),
        # Real Heading 1 for the section that follows.
        _heading(1, "1 Introduction"),
        # Real glossary section + table.
        _heading(2, "2 Glossary"),
        _table(
            3,
            headers=["Acronym", "Definition"],
            rows=[["GPP", "Generalized Pretend Protocol"]],
        ),
    ]
    profile = _profile(definitions_section_pattern=r"(?i)glossary")
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    # Glossary entries extracted from the real section's table, not
    # from the TOC entry that mentions "Glossary".
    assert "GPP" in tree.definitions_map
    # TOC drop count incremented for the TOC-styled entry.
    assert tree.parse_stats.toc_blocks_dropped >= 1


def test_definitions_table_header_fallback_only_when_section_lookup_fails():
    """When a section title DOES match, the label path wins and the
    table-header fallback is not consulted (avoids picking up a
    look-alike table that isn't the actual glossary)."""
    blocks = [
        _heading(0, "1 Glossary"),
        _table(
            1,
            headers=["Acronym", "Definition"],
            rows=[["GPP", "Global Pretend Protocol"]],
        ),
        # A second canonical-header table later in the doc — should be
        # ignored because the section title path matched first.
        _heading(2, "2 Other"),
        _table(
            3,
            headers=["Acronym", "Definition"],
            rows=[["XXX", "should-not-appear"]],
        ),
    ]
    profile = _profile(
        definitions_section_pattern=r"(?i)glossary",
        definitions_table_header_pattern=r"(?i)acronym\s*\|\s*definition",
    )
    tree = GenericStructuralParser(profile).parse(_doc(blocks))
    assert "GPP" in tree.definitions_map
    assert "XXX" not in tree.definitions_map


# ---------------------------------------------------------------------------
# Inline table ordering (faithful document position)
# ---------------------------------------------------------------------------

def test_tables_inlined_into_body_in_document_order():
    """A table between two paragraphs is inlined into req.text at its document
    position (not appended after all body text), so the synthesizer sees
    intro → table → note in the original order."""
    blocks = [
        _heading(0, "1 Frequency Bands"),
        _para(1, "The device shall support the following FR1 bands."),
        _table(2, headers=["Band", "BW (MHz)"], rows=[["n78", "100"], ["n77", "100"]]),
        _para(3, "Note: FR2 bands are specified separately."),
    ]
    tree = GenericStructuralParser(_profile()).parse(_doc(blocks))
    req = next(r for r in tree.requirements if "Frequency Bands" in (r.title or ""))
    text = req.text
    assert "| Band | BW (MHz) |" in text and "| n78 | 100 |" in text   # table inlined
    # faithful order: intro before the table before the trailing note
    assert text.index("following FR1 bands") < text.index("| n78 | 100 |") < text.index("FR2 bands")
    assert req.tables and req.tables[0].rows == [["n78", "100"], ["n77", "100"]]  # metadata kept
