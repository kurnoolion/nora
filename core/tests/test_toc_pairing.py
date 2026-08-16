"""Tests for the style-driven TOC pre-pass and section-number pairing.

Phase 3 of the generic-rules pivot. The DOCX TOC carries the document's
real section numbers (``"1.2.3"``) — body headings styled ``Heading N``
have only depth (from style suffix) and title text, no inline number.
The parser pre-pass walks ``toc N``-styled paragraphs to build an
index, then pairs each body heading against it during classification to
attach the actual section_number.

Test surface:

* ``TestTocIndex`` — pre-pass extraction (entry parsing, req_id peel
  with / without space, body block dropped at parse time).
* ``TestPairing`` — pair-by-req_id (primary), pair-by-title (fallback),
  miss-counter incrementation.
* ``TestDocxStylesClassification`` — ``Heading N`` style produces a
  Requirement at depth N with section_number from TOC; missing TOC
  entry yields empty section_number but still creates a Requirement.
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
    TocDetection,
)


_REQ_PATTERN = r"[A-Z]+_REQ_[A-Za-z0-9_]+_\d+"


def _profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="test_docx",
        profile_version=1,
        created_from=[],
        last_updated="2026-05-10",
        heading_detection=HeadingDetection(
            method="docx_styles",
            max_observed_depth=4,
        ),
        requirement_id=RequirementIdPattern(
            pattern=_REQ_PATTERN,
            anchor="last_run",
            normalize="upper",
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
        toc_detection=TocDetection(
            style_pattern=r"(?i)^toc\s+(\d+)$",
            entry_pattern=r"^(?P<num>[\w.]+)\t(?P<body>.+?)\t(?P<page>\d+)\s*$",
        ),
    )


def _toc_para(idx: int, text: str, depth: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        style=f"toc {depth}",
        font_info=FontInfo(size=10.0),
    )


def _heading(
    idx: int,
    depth: int,
    title: str,
    req_id: str,
    *,
    page: int = 5,
    block_type: BlockType = BlockType.PARAGRAPH,
) -> ContentBlock:
    return ContentBlock(
        type=block_type,
        position=Position(page=page, index=idx),
        text=f"{title}{req_id}",
        style=f"Heading {depth}",
        level=depth if block_type == BlockType.HEADING else None,
        font_info=FontInfo(size=14.0, bold=True),
        runs=[
            TextRun(text=title, struck=False),
            TextRun(text=req_id, struck=False),
        ],
    )


def _para(idx: int, text: str, *, page: int = 5) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=page, index=idx),
        text=text,
        style="div",
        font_info=FontInfo(size=11.0),
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(
        source_file="fixture.docx",
        source_format="docx",
        mno="MNO0",
        release="r1",
        doc_type="requirement",
        content_blocks=blocks,
    )


def _parse(blocks: list[ContentBlock]):
    return GenericStructuralParser(_profile()).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# TOC index pre-pass
# ---------------------------------------------------------------------------


class TestTocIndex:
    def test_index_built_from_toc_styled_paragraphs(self):
        blocks = [
            _toc_para(0, "1\tIntroduction VZ_REQ_FOO_1\t10", depth=1),
            _toc_para(1, "1.1\tScope VZ_REQ_FOO_2\t11", depth=2),
            _heading(2, 1, "Introduction ", "VZ_REQ_FOO_1"),
        ]
        parser = GenericStructuralParser(_profile())
        index, toc_blocks = parser._extract_toc_index(_doc(blocks))
        # Two TOC blocks indexed.
        assert toc_blocks == {0, 1}
        # Primary (req_id) keys present, normalize=upper applied.
        assert "rid:VZ_REQ_FOO_1" in index
        assert "rid:VZ_REQ_FOO_2" in index
        # Section numbers preserved literally from TOC.
        assert index["rid:VZ_REQ_FOO_1"].section_number == "1"
        assert index["rid:VZ_REQ_FOO_2"].section_number == "1.1"

    def test_no_space_between_title_and_req_id(self):
        """TOC body may have no space before the req_id — peel still works."""
        blocks = [
            _toc_para(0, "1.1\tScopeVZ_REQ_FOO_2\t11", depth=2),
        ]
        parser = GenericStructuralParser(_profile())
        index, _ = parser._extract_toc_index(_doc(blocks))
        entry = index["rid:VZ_REQ_FOO_2"]
        assert entry.section_number == "1.1"
        assert entry.title == "Scope"

    def test_mixed_case_req_id_normalized_upper(self):
        blocks = [
            _toc_para(0, "1.1\tWiFi VZ_REQ_VoWiFi_99\t11", depth=2),
        ]
        parser = GenericStructuralParser(_profile())
        index, _ = parser._extract_toc_index(_doc(blocks))
        # Stored under upper-cased key.
        assert "rid:VZ_REQ_VOWIFI_99" in index
        assert "rid:VZ_REQ_VoWiFi_99" not in index

    def test_toc_blocks_dropped_at_parse_time(self):
        blocks = [
            _toc_para(0, "1\tIntro VZ_REQ_FOO_1\t10", depth=1),
            _heading(1, 1, "Intro ", "VZ_REQ_FOO_1"),
            _para(2, "body content"),
        ]
        tree = _parse(blocks)
        assert tree.parse_stats.toc_blocks_dropped == 1
        # Only the body heading creates a Requirement; the TOC block
        # is harvested for metadata then dropped.
        assert len(tree.requirements) == 1


# ---------------------------------------------------------------------------
# Heading ↔ TOC pairing
# ---------------------------------------------------------------------------


class TestPairing:
    def test_pair_by_req_id_attaches_section_number(self):
        blocks = [
            _toc_para(0, "1.2.3\tFoo Section VZ_REQ_FOO_42\t15", depth=3),
            _heading(1, 3, "Foo Section ", "VZ_REQ_FOO_42"),
            _para(2, "body"),
        ]
        tree = _parse(blocks)
        assert len(tree.requirements) == 1
        assert tree.requirements[0].section_number == "1.2.3"
        assert tree.requirements[0].req_id == "VZ_REQ_FOO_42"

    def test_pair_by_title_fallback_when_no_req_id_in_toc(self):
        """TOC entry lacks req_id; pair-by-title still works."""
        blocks = [
            _toc_para(0, "2\tOverview \t20", depth=1),  # body has no req_id
            _heading(1, 1, "Overview ", "VZ_REQ_FOO_5"),  # heading has req_id
            _para(2, "body"),
        ]
        tree = _parse(blocks)
        assert tree.requirements[0].section_number == "2"
        assert tree.requirements[0].req_id == "VZ_REQ_FOO_5"

    def test_miss_counter_increments_on_unmatched_heading(self):
        blocks = [
            _toc_para(0, "1\tIntro VZ_REQ_X_1\t10", depth=1),
            # Body heading with no matching TOC entry (different req_id, different title).
            _heading(1, 2, "Unrelated ", "VZ_REQ_X_99"),
            _para(2, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_stats.toc_pair_misses == 1
        # Heading still produces a Requirement (with empty section_number).
        assert len(tree.requirements) == 1
        assert tree.requirements[0].section_number == ""
        assert tree.requirements[0].req_id == "VZ_REQ_X_99"

    def test_zero_misses_when_all_paired(self):
        blocks = [
            _toc_para(0, "1\tIntro VZ_REQ_X_1\t10", depth=1),
            _toc_para(1, "1.1\tScope VZ_REQ_X_2\t11", depth=2),
            _heading(2, 1, "Intro ", "VZ_REQ_X_1"),
            _heading(3, 2, "Scope ", "VZ_REQ_X_2"),
            _para(4, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_stats.toc_pair_misses == 0

    def test_level1_chapter_heading_after_heading_anchors(self):
        """The heading-continuation defense is numbering-path-only
        (strand req-recall, msg 0020): a styled level-1 chapter heading
        directly following another heading (e.g. an empty trailing
        subsection) must create its own anchored section, never be
        swallowed into the predecessor's title — Word's Heading styles
        are authoritative, so the PDF split-line artifact the defense
        targets cannot occur here."""
        blocks = [
            _toc_para(0, "12\tOld Chapter\t40", depth=1),
            _toc_para(1, "12.9\tReserved\t48", depth=2),
            _toc_para(2, "13\tNetwork VZ_REQ_NET_13\t50", depth=1),
            _heading(3, 1, "Old Chapter", ""),
            _heading(4, 2, "Reserved", ""),        # empty trailing subsection
            _heading(5, 1, "Network ", "VZ_REQ_NET_13"),
            _para(6, "Chapter preamble."),
        ]
        tree = _parse(blocks)
        chapter = next(
            (r for r in tree.requirements if r.section_number == "13"), None
        )
        assert chapter is not None
        assert chapter.req_id == "VZ_REQ_NET_13"
        reserved = next(
            r for r in tree.requirements if r.section_number == "12.9"
        )
        assert reserved.title == "Reserved"        # nothing swallowed


# ---------------------------------------------------------------------------
# docx_styles classification path
# ---------------------------------------------------------------------------


class TestDocxStylesClassification:
    def test_heading_n_style_recognized_as_heading(self):
        blocks = [
            _toc_para(0, "1\tFoo VZ_REQ_X_1\t10", depth=1),
            _toc_para(1, "1.1\tBar VZ_REQ_X_2\t11", depth=2),
            _heading(2, 1, "Foo ", "VZ_REQ_X_1"),
            _heading(3, 2, "Bar ", "VZ_REQ_X_2"),
            _para(4, "body"),
        ]
        tree = _parse(blocks)
        assert len(tree.requirements) == 2
        # First Requirement is parent of second per depth.
        assert tree.requirements[0].section_number == "1"
        assert tree.requirements[1].section_number == "1.1"
        assert tree.requirements[1].parent_req_id == "VZ_REQ_X_1"

    def test_heading_without_toc_match_still_creates_requirement(self):
        """Empty section_number is allowed — a docx_styles heading
        produces a Requirement with section_number='' when TOC pairing
        misses. Critical: this does NOT collide with other empty
        section_numbers via the dedup set (every miss → its own
        Requirement)."""
        blocks = [
            _heading(0, 2, "First Section ", "VZ_REQ_X_1"),
            _para(1, "body"),
            _heading(2, 2, "Second Section ", "VZ_REQ_X_2"),
            _para(3, "body"),
        ]
        tree = _parse(blocks)
        # Both headings produce Requirements despite empty section_numbers.
        assert len(tree.requirements) == 2
        assert tree.requirements[0].req_id == "VZ_REQ_X_1"
        assert tree.requirements[1].req_id == "VZ_REQ_X_2"
        assert tree.parse_stats.toc_pair_misses == 2

    def test_title_strips_last_run_when_anchor_last_run(self):
        blocks = [
            _toc_para(0, "1\tFoo Title VZ_REQ_X_1\t10", depth=1),
            _heading(1, 1, "Foo Title ", "VZ_REQ_X_1"),
        ]
        tree = _parse(blocks)
        # Title should be the runs[:-1] joined, not the full text.
        assert tree.requirements[0].title.strip() == "Foo Title"

    def test_concatenated_single_run_falls_back_to_text(self, caplog):
        """Mirror of the work-PC malformed body heading: ``runs`` has
        one TextRun whose text holds both the title and the req_id
        concatenated (DOCX run-splitter didn't fire — typically a
        copy-paste artifact). The last_run anchor fails because the
        run text doesn't solo-match the req_id pattern. Parser must
        fall back to text-search and log under
        ``parser.format_error: kind=concatenated_run_heading``."""
        bad_heading = ContentBlock(
            type=BlockType.HEADING,
            position=Position(page=5, index=1),
            text="1.1.1.Some Title VZ_REQ_FOO_42",
            level=3,
            style="Heading 3",
            font_info=FontInfo(size=14.0, bold=True),
            runs=[
                TextRun(text="1.1.1.Some Title VZ_REQ_FOO_42", struck=False),
            ],
        )
        blocks = [
            _toc_para(0, "4.1.1\t1.1.1.Some Title VZ_REQ_FOO_42\t13", depth=3),
            bad_heading,
            _para(2, "body content"),
        ]
        import logging
        with caplog.at_level(logging.WARNING):
            tree = _parse(blocks)
        assert len(tree.requirements) == 1
        r = tree.requirements[0]
        assert r.req_id == "VZ_REQ_FOO_42"
        assert r.section_number == "4.1.1"
        assert tree.parse_stats.toc_pair_misses == 0
        format_errors = [
            r for r in caplog.records
            if r.message.startswith("parser.format_error: kind=concatenated_run_heading")
        ]
        assert len(format_errors) >= 1

    def test_empty_runs_falls_back_to_text_for_req_id_and_title(self, caplog):
        """When body heading has ``runs=[]`` (DOCX formatting error
        in source), the parser falls back to ``block.text`` for both
        req_id extraction and title-stripping. Pair-by-req_id then
        succeeds, the heading produces a Requirement with proper
        section_number, and a ``parser.format_error`` WARN is logged."""
        # Body heading: same shape as work-PC missing_toc_reqs.json
        # — runs empty, text contains both title-prefix and req_id.
        bad_heading = ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=5, index=1),
            text="1.1.1.Some Title VZ_REQ_FOO_42",
            style="Heading 3",
            font_info=FontInfo(size=14.0, bold=True),
            runs=[],  # empty — the format error
        )
        blocks = [
            _toc_para(0, "4.1.1\t1.1.1.Some Title VZ_REQ_FOO_42\t13", depth=3),
            bad_heading,
            _para(2, "body content"),
        ]
        import logging
        with caplog.at_level(logging.WARNING):
            tree = _parse(blocks)
        # Requirement extracted; section_number from TOC; req_id from text fallback.
        assert len(tree.requirements) == 1
        r = tree.requirements[0]
        assert r.req_id == "VZ_REQ_FOO_42"
        assert r.section_number == "4.1.1"
        # No TOC pair miss — pair-by-req_id succeeded.
        assert tree.parse_stats.toc_pair_misses == 0
        # Format error was logged.
        format_errors = [
            r for r in caplog.records
            if r.message.startswith("parser.format_error: kind=empty_runs_heading")
        ]
        assert len(format_errors) >= 1

    def test_heading_blocktype_routes_through_classifier(self):
        """Real DOCX extractor emits ``BlockType.HEADING`` for
        Word-styled headings (per ``docx_extractor._paragraph_block``).
        Regression guard: HEADING-typed blocks must reach the heading
        classifier the same way PARAGRAPH-typed blocks do — earlier
        the body pass only entered the heading-creation branch when
        ``block.type == BlockType.PARAGRAPH``, leaving DOCX heading
        blocks unrouted (work-PC corpus produced reqs=0)."""
        blocks = [
            _toc_para(0, "1\tFoo VZ_REQ_X_1\t10", depth=1),
            _heading(1, 1, "Foo ", "VZ_REQ_X_1", block_type=BlockType.HEADING),
            _para(2, "body content"),
        ]
        tree = _parse(blocks)
        assert len(tree.requirements) == 1
        assert tree.requirements[0].section_number == "1"
        assert tree.requirements[0].req_id == "VZ_REQ_X_1"
        assert tree.requirements[0].title.strip() == "Foo"


# ---------------------------------------------------------------------------
# Parse-log + RPT integration (counter flows into compact report)
# ---------------------------------------------------------------------------


class TestParseLogIntegration:
    def test_summary_carries_toc_pair_misses_count(self):
        """``ParseLogSummary.toc_pair_misses`` mirrors
        ``ParseStats.toc_pair_misses``; per-miss list lives on
        ``ParseLog.toc_pair_misses`` for local diagnostics."""
        blocks = [
            _toc_para(0, "1\tIntro VZ_REQ_X_1\t10", depth=1),
            _heading(1, 2, "Unrelated ", "VZ_REQ_X_99"),  # miss
            _heading(2, 2, "Also Unrelated ", "VZ_REQ_X_98"),  # miss
            _para(3, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_stats.toc_pair_misses == 2
        assert tree.parse_log.summary.toc_pair_misses == 2
        assert len(tree.parse_log.toc_pair_misses) == 2
        # Per-miss entries carry the title locally — never inlined into RPT.
        miss_req_ids = {m.req_id for m in tree.parse_log.toc_pair_misses}
        assert miss_req_ids == {"VZ_REQ_X_99", "VZ_REQ_X_98"}

    def test_zero_misses_emits_no_entries(self):
        blocks = [
            _toc_para(0, "1\tIntro VZ_REQ_X_1\t10", depth=1),
            _heading(1, 1, "Intro ", "VZ_REQ_X_1"),
            _para(2, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_log.summary.toc_pair_misses == 0
        assert tree.parse_log.toc_pair_misses == []


# ---------------------------------------------------------------------------
# Front-matter cutoff (Phase 4)
# ---------------------------------------------------------------------------


def _revhist_label(idx: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text="Revision History",
        style="div",
        font_info=FontInfo(size=12.0),
        runs=[TextRun("Revision History", struck=False)],
    )


def _table(idx: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=1, index=idx),
        headers=["Date", "Change"],
        rows=[["2026-01-01", "Initial"]],
    )


class TestFrontMatterCutoff:
    def test_doc_title_heading_before_revhist_dropped(self):
        """Work-PC layout: doc title heading sits in front matter
        before the revision-history section. With the style-driven TOC
        path enabled, everything ≤ revhist_end is dropped."""
        blocks = [
            _heading(0, 1, "Doc Title ", "VZ_REQ_X_1"),  # doc-title heading
            _revhist_label(1),
            _table(2),
            _heading(3, 1, "Real Section ", "VZ_REQ_X_2"),  # body content
            _para(4, "body content", page=2),
        ]
        # Add a TOC entry so style-driven path is active and pairs the body heading.
        blocks.insert(0, _toc_para(0, "1\tReal Section VZ_REQ_X_2\t5", depth=1))
        tree = _parse(blocks)
        # Only the post-cutoff body heading produces a Requirement.
        assert len(tree.requirements) == 1
        assert tree.requirements[0].req_id == "VZ_REQ_X_2"
        # Counters: TOC, revhist (label + table), front_matter (doc title).
        assert tree.parse_stats.toc_blocks_dropped == 1
        assert tree.parse_stats.revhist_blocks_dropped == 2
        assert tree.parse_stats.frontmatter_blocks_dropped == 1

    def test_cutoff_disabled_when_style_path_off(self):
        """OA-style corpora (no ``toc_detection.style_pattern``) keep
        legacy behavior — revhist drop is inline only; chapter
        headings before revhist are never dropped."""
        # Build profile WITHOUT style_pattern.
        profile = _profile()
        profile.toc_detection.style_pattern = ""
        profile.heading_detection.method = "numbering"
        profile.heading_detection.numbering_pattern = r"^(\d+(?:\.\d+)*)\s+\S"

        # Use plain numbered text (no runs) so the numbering path applies.
        chapter = ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=0),
            text="1 LTE Data Retry",
            font_info=FontInfo(size=14.0, bold=True),
        )
        scope = ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=3),
            text="1.1 Scope",
            font_info=FontInfo(size=12.0, bold=True),
        )
        blocks = [
            chapter,
            _revhist_label(1),
            _table(2),
            scope,
            _para(4, "body"),
        ]
        for i, b in enumerate(blocks):
            b.position.index = i
        tree = GenericStructuralParser(profile).parse(_doc(blocks))
        section_nums = [r.section_number for r in tree.requirements]
        assert section_nums == ["1", "1.1"]  # chapter retained
        assert tree.parse_stats.frontmatter_blocks_dropped == 0

    def test_cutoff_zero_when_no_front_matter(self):
        """Empty front matter (no TOC, no revhist) → no cutoff,
        nothing dropped beyond ordinary mechanisms."""
        blocks = [
            _heading(0, 1, "First Section ", "VZ_REQ_X_1"),
            _para(1, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_stats.frontmatter_blocks_dropped == 0
        assert tree.parse_stats.revhist_blocks_dropped == 0
        assert tree.parse_stats.toc_blocks_dropped == 0
        assert len(tree.requirements) == 1

    def test_docx_heading_styled_revhist_with_trailing_req_id_detected(self):
        """Real work-PC corpus shape (commit 6298333 era): the revhist
        label is a DOCX ``Heading 1`` block whose text appends a
        trailing req_id run, e.g. ``REVISION HISTORY <MNO>_REQ_..._1234``.
        Earlier code matched ``b.text.strip()`` against a regex
        anchored with ``\\s*$`` — the trailing req_id broke the match.
        Plus the PARAGRAPH-only type filter excluded HEADING blocks
        entirely. Fix uses ``_heading_title_text`` (strips the
        trailing req_id run when ``anchor=last_run``) and accepts
        both block types."""
        revhist_heading = ContentBlock(
            type=BlockType.HEADING,
            position=Position(page=1, index=10),
            text="REVISION HISTORY VZ_REQ_FOO_1234",
            level=1,
            style="Heading 1",
            font_info=FontInfo(size=14.0, bold=True),
            runs=[
                TextRun(text="REVISION HISTORY", struck=False),
                TextRun(text=" ", struck=False),
                TextRun(text="VZ_REQ_FOO_1234", struck=False),
            ],
        )
        blocks = [
            _toc_para(0, "1\tSection One VZ_REQ_FOO_5\t5", depth=1),
            revhist_heading,
            _table(11),  # the revhist table itself
            _heading(12, 1, "Section One ", "VZ_REQ_FOO_5"),
            _para(13, "body"),
        ]
        tree = _parse(blocks)
        # Revhist label + table both dropped — 2 blocks.
        assert tree.parse_stats.revhist_blocks_dropped == 2
        # Body heading still produces a Requirement.
        section_ids = {r.req_id for r in tree.requirements}
        assert "VZ_REQ_FOO_5" in section_ids
        # The revhist heading's own req_id (VZ_REQ_FOO_1234) is not
        # promoted because the heading is dropped before classification.
        assert "VZ_REQ_FOO_1234" not in section_ids

    def test_heading_after_revhist_table_not_swept_into_revhist(self):
        """Regression guard: when the next block after the revhist table
        is ``BlockType.HEADING`` (DOCX extractor's typing for Word
        Heading-styled paragraphs), the consume must break — otherwise
        the heading is silently included in ``revhist_blocks_dropped``,
        the front-matter cutoff is extended past where it should be,
        and downstream sections get truncated."""
        blocks = [
            _toc_para(0, "1\tReal VZ_REQ_X_1\t5", depth=1),
            _revhist_label(1),
            _table(2),
            _heading(
                3, 1, "Real ", "VZ_REQ_X_1", block_type=BlockType.HEADING
            ),
            _para(4, "body"),
        ]
        tree = _parse(blocks)
        # revhist range is just label + table — 2 blocks, NOT 3.
        assert tree.parse_stats.revhist_blocks_dropped == 2
        # Heading is preserved as a Requirement (not consumed).
        assert len(tree.requirements) == 1
        assert tree.requirements[0].req_id == "VZ_REQ_X_1"

    def test_summary_carries_frontmatter_count(self):
        # Layout: TOC at 0, doc-title heading at 1, revhist at 2-3,
        # real body heading at 4. Cutoff = max(0, 3) = 3, so the doc
        # title heading at index 1 is the front_matter drop.
        blocks = [
            _toc_para(0, "1\tReal VZ_REQ_X_2\t5", depth=1),
            _heading(1, 1, "Doc Title ", "VZ_REQ_X_1"),
            _revhist_label(2),
            _table(3),
            _heading(4, 1, "Real ", "VZ_REQ_X_2"),
            _para(5, "body"),
        ]
        tree = _parse(blocks)
        assert tree.parse_log.summary.frontmatter_blocks_dropped == 1
