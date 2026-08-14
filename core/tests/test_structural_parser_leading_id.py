"""Tests for leading-id body-block requirement detection (D-DRAFT-2).

Hand-crafted in-memory DocumentIR + DocumentProfile fixtures — no PDF
extraction, no real-doc dependency. Exercises
``requirement_id.detection_mode == "leading_id_body"``: a corpus whose
sections/subsections carry no req_ids and whose requirements are flat body
paragraphs that BEGIN with the req_id (``<PREFIX>-<PLAN>-<DIGITS>``).

Generic example ids only (``ABC-FOO-001``) — no real corpus plan codes.
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
# Fixture helpers
# ---------------------------------------------------------------------------


def _profile(detection_mode: str = "leading_id_body") -> DocumentProfile:
    return DocumentProfile(
        profile_name="test-leading-id",
        profile_version=1,
        created_from=[],
        last_updated="2026-06-14",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[
                HeadingLevel(level=1, font_size_min=13.0, font_size_max=15.0, bold=True),
            ],
            numbering_pattern=r"^\d+(\.\d+)*\s",
            max_observed_depth=3,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"ABC-[A-Z]+-\d+",
            components={"separator": "-", "plan_id_position": 1, "number_position": 2},
            anchor="leading_text",
            detection_mode=detection_mode,
            sample_ids=[],
            total_found=0,
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _heading(idx: int, text: str) -> ContentBlock:
    """Paragraph block with heading-sized bold font — a non-requirement
    section heading in the leading-id model."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
    )


def _body(idx: int, text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=12.0, bold=False),
    )


def _doc(blocks: list[ContentBlock]) -> DocumentIR:
    for i, b in enumerate(blocks):
        b.position.index = i
    return DocumentIR(
        source_file="fixture.pdf",
        source_format="pdf",
        mno="MNOB",
        release="Jun2026",
        doc_type="requirement",
        content_blocks=blocks,
    )


# A document shaped like MNO-B: headings have no req_ids; requirements are
# flat body paragraphs leading with their id; one requirement's text spills
# over to a continuation block; one requirement mentions another inline.
def _mnob_blocks() -> list[ContentBlock]:
    return [
        _heading(0, "1 General"),
        _heading(1, "1.1 Device Requirements"),
        _body(2, "This subsection covers device behavior."),       # preamble
        _body(3, "ABC-FOO-001 The device shall support IPv6."),    # req
        _body(4, "It shall also support IPv4."),                   # continuation of 001
        _body(5, "ABC-FOO-002 The device shall comply with ABC-FOO-001 dual-stack."),  # req + inline ref
        _heading(6, "1.2 Network Requirements"),
        _body(7, "Network section intro."),                        # preamble
        _body(8, "ABC-BAR-010 The network shall provide DNS."),    # req under new heading
    ]


def _parse(blocks, detection_mode="leading_id_body"):
    return GenericStructuralParser(_profile(detection_mode)).parse(_doc(blocks))


def _leading_reqs(tree):
    """Requirements created by leading-id detection: a req_id and no own
    section_number (section headings keep their section_number)."""
    return [r for r in tree.requirements if r.req_id and not r.section_number]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLeadingIdDetection:
    def test_each_leading_id_paragraph_becomes_a_requirement(self):
        tree = _parse(_mnob_blocks())
        ids = {r.req_id for r in _leading_reqs(tree)}
        assert ids == {"ABC-FOO-001", "ABC-FOO-002", "ABC-BAR-010"}

    def test_inline_reference_does_not_spawn_phantom(self):
        # ABC-FOO-002's body mentions ABC-FOO-001 mid-sentence; only three
        # leading-id requirements exist (no phantom for the inline mention).
        tree = _parse(_mnob_blocks())
        assert len(_leading_reqs(tree)) == 3

    def test_requirement_parented_to_enclosing_heading(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.section_number == ""          # not a section of its own
        assert foo1.parent_section == "1.1"        # the enclosing subsection

    def test_continuation_block_appends_to_requirement(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert "support IPv6" in foo1.text
        assert "support IPv4" in foo1.text         # the continuation block

    def test_preamble_lands_on_heading_not_a_requirement(self):
        tree = _parse(_mnob_blocks())
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert "covers device behavior" in sub.text
        # the preamble text is not a leading-id requirement
        assert all(
            "covers device behavior" not in r.text for r in _leading_reqs(tree)
        )

    def test_heading_reset_reparents_after_new_section(self):
        # ABC-BAR-010 sits under 1.2 — the cursor reset on the 1.2 heading
        # must reparent it there, not leave it under 1.1.
        tree = _parse(_mnob_blocks())
        bar = next(r for r in tree.requirements if r.req_id == "ABC-BAR-010")
        assert bar.parent_section == "1.2"

    def test_hierarchy_path_inherited_from_parent_heading(self):
        tree = _parse(_mnob_blocks())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.hierarchy_path == ["General", "Device Requirements"]

    def test_heading_nodes_have_no_req_id(self):
        # Section headings are non-requirement context in this mode.
        tree = _parse(_mnob_blocks())
        for r in tree.requirements:
            if r.section_number:
                assert r.req_id == ""


class TestPerRequirementPlanId:
    """D-DRAFT-1: each requirement carries its own ``plan_id`` extracted from
    its req_id (`<PREFIX>-<PLAN>-<DIGITS>`, plan at component position 1), so a
    single document carrying multiple plans is groupable downstream."""

    def test_each_requirement_gets_its_own_plan(self):
        tree = _parse(_mnob_blocks())
        plans = {r.req_id: r.plan_id for r in _leading_reqs(tree)}
        assert plans == {
            "ABC-FOO-001": "FOO",
            "ABC-FOO-002": "FOO",
            "ABC-BAR-010": "BAR",
        }

    def test_document_carries_multiple_plans(self):
        tree = _parse(_mnob_blocks())
        assert {r.plan_id for r in _leading_reqs(tree)} == {"FOO", "BAR"}

    def test_heading_nodes_fall_back_to_tree_plan(self):
        # Structural headings have no req_id → no extractable plan → they take
        # the tree-level plan_id (empty here, since the fixture sets no plan
        # metadata pattern). The point: population never crashes on id-less nodes.
        tree = _parse(_mnob_blocks())
        for r in tree.requirements:
            if r.section_number:  # a heading node
                assert r.plan_id == tree.plan_id


class TestDefaultHeadingModeUnchanged:
    """With the default ``detection_mode == "heading"``, leading-id body
    paragraphs do NOT become standalone requirements — they append to the
    enclosing heading and the first inline id becomes that heading's id.
    Guards the MNO-A (default) path against regression."""

    def test_no_standalone_leading_requirements(self):
        tree = _parse(_mnob_blocks(), detection_mode="heading")
        # No section_number-less req nodes were created by leading-id logic.
        assert _leading_reqs(tree) == []

    def test_inline_id_attaches_to_heading(self):
        tree = _parse(_mnob_blocks(), detection_mode="heading")
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        # Heading 1.1 had no id; the first body id is captured as its req_id.
        assert sub.req_id == "ABC-FOO-001"


# ── D-DRAFT-5: title/body split + ancestor-section Context ───────────


def _req(idx: int, header: str, *body_lines: str) -> ContentBlock:
    """A leading-id requirement block with preserved source lines:
    lines[0] = '<req_id> <title>', the rest = body."""
    lines = [header, *body_lines]
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=" ".join(lines),
        font_info=FontInfo(size=12.0, bold=False),
        lines=lines,
    )


class TestTitleBodySplit:
    def test_title_and_body_separated_from_lines(self):
        blocks = [
            _heading(0, "3 Requirements"),
            _heading(1, "3.1 Registration"),
            _req(2, "ABC-FOO-001 Re-registration timer",
                 "The device shall re-register within 30 seconds."),
        ]
        tree = _parse(blocks)
        r = next(x for x in tree.requirements if x.req_id == "ABC-FOO-001")
        assert r.title == "Re-registration timer"
        assert r.text == "The device shall re-register within 30 seconds."
        assert "ABC-FOO-001" not in r.text           # req_id not duplicated into body
        assert "Re-registration timer" not in r.text  # title not duplicated into body

    def test_no_lines_falls_back_to_whole_block(self):
        # _body() blocks carry no `lines` — old behavior: empty title, whole
        # block as text (back-compat for extractors without line structure).
        tree = _parse(_mnob_blocks())
        r = next(x for x in tree.requirements if x.req_id == "ABC-FOO-001")
        assert r.title == ""
        assert "support IPv6" in r.text


class TestBuildContextString:
    """D-DRAFT-5: the generic context helper (consumed by the SIRA adapter +
    NORA chunk builder). section_index = {section_number: (title, body)}."""

    SECTIONS = {
        "5": ("Bands", "Chapter 5 intro."),
        "5.1": ("Frequency", "Frequency intro."),
        "5.1.2": ("LTE", "LTE intro."),
    }

    def test_none_mode_is_empty(self):
        from core.src.parser.structural_parser import build_context_string
        assert build_context_string("5.1.2", self.SECTIONS, "none") == ""

    def test_path_mode_numbers_and_titles_no_body(self):
        from core.src.parser.structural_parser import build_context_string
        out = build_context_string("5.1.2", self.SECTIONS, "path")
        # Single-line breadcrumb wrapped in a [Context: …] label.
        assert out == "[Context: 5 Bands > 5.1 Frequency > 5.1.2 LTE]"
        assert "intro" not in out  # body excluded in path mode

    def test_path_and_content_includes_numbering_and_body(self):
        from core.src.parser.structural_parser import build_context_string
        out = build_context_string("5.1.2", self.SECTIONS, "path_and_content")
        # One bracketed header per ancestor, each followed by its body.
        assert out == (
            "[5 Bands]\nChapter 5 intro.\n"
            "[5.1 Frequency]\nFrequency intro.\n"
            "[5.1.2 LTE]\nLTE intro."
        )

    def test_empty_parent_section_is_empty(self):
        from core.src.parser.structural_parser import build_context_string
        assert build_context_string("", self.SECTIONS, "path") == ""

    def test_missing_ancestor_is_skipped(self):
        from core.src.parser.structural_parser import build_context_string
        idx = {"5": ("Bands", ""), "5.1.2": ("LTE", "")}  # 5.1 missing
        assert build_context_string("5.1.2", idx, "path") == "[Context: 5 Bands > 5.1.2 LTE]"

    def test_max_chars_caps_each_ancestor_body(self):
        from core.src.parser.structural_parser import build_context_string
        idx = {"5": ("Bands", "X" * 5000), "5.1": ("Freq", "Y" * 100)}
        out = build_context_string("5.1", idx, "path_and_content", max_chars=2000)
        assert "X" * 2000 + "…" in out      # long body capped to 2000 + ellipsis
        assert "X" * 2001 not in out
        assert "Y" * 100 in out             # short body untouched

    def test_max_chars_zero_is_uncapped(self):
        from core.src.parser.structural_parser import build_context_string
        idx = {"5": ("Bands", "X" * 5000)}
        out = build_context_string("5", idx, "path_and_content", max_chars=0)
        assert "X" * 5000 in out

    def test_path_mode_unaffected_by_max_chars(self):
        from core.src.parser.structural_parser import build_context_string
        idx = {"5": ("Bands", "X" * 5000)}
        assert build_context_string("5", idx, "path", max_chars=10) == "[Context: 5 Bands]"


class TestContextNotMaterializedInTree:
    def test_tree_does_not_carry_per_req_context(self):
        # D-DRAFT-5: context is assembled by consumers, not stored per-req —
        # the parsed tree stays compact.
        blocks = [
            _heading(0, "3 Bands"),
            _body(1, "Chapter 3 intro."),
            _heading(2, "3.1 LTE"),
            _req(3, "ABC-FOO-001 Band 13", "Device shall support band 13."),
        ]
        tree = _parse(blocks)
        assert all(r.context == "" for r in tree.requirements)
        assert tree.build_context == "none"  # test profile leaves the default


# ── strand mno-b-tables: TABLE/IMAGE attach to the leading-id requirement ──


def _table(idx: int, headers: list[str], rows: list[list[str]]) -> ContentBlock:
    return ContentBlock(
        type=BlockType.TABLE,
        position=Position(page=1, index=idx),
        headers=headers,
        rows=rows,
        font_info=FontInfo(size=10.0),
    )


def _image(idx: int, path: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.IMAGE,
        position=Position(page=1, index=idx),
        image_path=path,
        surrounding_text="",
        font_info=FontInfo(size=10.0),
    )


class TestTableAttachmentLeadingIdMode:
    """A table (or image) following a leading-id requirement belongs to that
    requirement, not the enclosing non-requirement heading — section nodes
    have no req_id, and downstream chunk consumers drop id-less nodes, so a
    section-attached table vanishes from the corpus."""

    def _blocks_table_after_req(self) -> list[ContentBlock]:
        return [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support the bearers below."),
            _table(3, ["Bearer", "Mode"], [["b1", "m1"], ["b2", "m2"]]),
            _body(4, "ABC-FOO-002 Unrelated follow-on requirement."),
        ]

    def test_table_attaches_to_preceding_requirement(self):
        tree = _parse(self._blocks_table_after_req())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(foo1.tables) == 1
        assert foo1.tables[0].headers == ["Bearer", "Mode"]

    def test_table_not_on_the_section_heading(self):
        tree = _parse(self._blocks_table_after_req())
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert sub.tables == []

    def test_table_inlined_into_requirement_text_in_order(self):
        # The markdown rendering lands in the REQUIREMENT's body, after its
        # prose (document order preserved for the synthesizer).
        tree = _parse(self._blocks_table_after_req())
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert "Bearer" in foo1.text and "b2" in foo1.text
        assert foo1.text.index("bearers below") < foo1.text.index("Bearer")

    def test_table_after_continuation_still_attaches_to_requirement(self):
        # req → continuation paragraph → table: the cursor survives the
        # continuation, so the table is still the requirement's.
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support the bearers below."),
            _body(3, "Additional continuation prose."),
            _table(4, ["Bearer"], [["b1"]]),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(foo1.tables) == 1

    def test_table_under_fresh_heading_attaches_to_heading(self):
        # req in 1.1, then heading 1.2 immediately followed by a table: the
        # fresh heading resets the cursor — the table is 1.2's, NOT a stale
        # requirement's from the previous section.
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _heading(3, "1.2 Applicability Matrix"),
            _table(4, ["Plan", "Applies"], [["p", "yes"]]),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        sec12 = next(r for r in tree.requirements if r.section_number == "1.2")
        assert foo1.tables == []
        assert len(sec12.tables) == 1

    def test_table_in_section_preamble_attaches_to_heading(self):
        # heading → preamble prose → table, before any requirement: no cursor
        # yet, so the table falls back to the section.
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "Intro prose without a leading id."),
            _table(3, ["Col"], [["v"]]),
            _body(4, "ABC-FOO-001 The requirement follows the table."),
        ]
        tree = _parse(blocks)
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(sub.tables) == 1
        assert foo1.tables == []

    def test_image_attaches_to_preceding_requirement(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 See the reference diagram."),
            _image(3, "images/fig1.png"),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert [i.path for i in foo1.images] == ["images/fig1.png"]
        assert sub.images == []

    def test_image_under_fresh_heading_attaches_to_heading(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _heading(3, "1.2 Reference Figures"),
            _image(4, "images/fig2.png"),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        sec12 = next(r for r in tree.requirements if r.section_number == "1.2")
        assert foo1.images == []
        assert [i.path for i in sec12.images] == ["images/fig2.png"]

    def test_heading_mode_table_still_attaches_to_section(self):
        # Regression guard for the default (MNO-A) path: in heading mode the
        # table stays on the enclosing section, exactly as before.
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support the bearers below."),
            _table(3, ["Bearer"], [["b1"]]),
        ]
        tree = _parse(blocks, detection_mode="heading")
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert len(sub.tables) == 1


class TestEmptyTableGuard:
    # Empty-table guard (strand table-fidelity): a TABLE block with neither
    # html nor any grid content is skipped entirely — no TableData stored,
    # no empty inline appended, no effect on attachment cursors.

    def test_empty_table_not_stored_on_requirement(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _table(3, [], []),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.tables == []

    def test_whitespace_only_table_not_stored(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _table(3, ["", " "], [["", "  "]]),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert foo1.tables == []
        assert foo1.text.rstrip() == foo1.text  # no trailing empty inline

    def test_empty_table_not_stored_in_heading_mode(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support the bearers."),
            _table(3, [], []),
        ]
        tree = _parse(blocks, detection_mode="heading")
        sub = next(r for r in tree.requirements if r.section_number == "1.1")
        assert sub.tables == []

    def test_phantom_table_invisible_to_attachment(self):
        # req -> empty table -> real table: the real table still attaches
        # to the requirement (the phantom neither claims it nor clears the
        # requirement cursor).
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _table(3, [], []),
            _table(4, ["Bearer"], [["b1"]]),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(foo1.tables) == 1
        assert foo1.tables[0].headers == ["Bearer"]

    def test_html_only_table_is_kept(self):
        # A provider table with HTML but an empty flat grid has content —
        # the guard must not skip it.
        html_block = ContentBlock(
            type=BlockType.TABLE,
            position=Position(page=1, index=3),
            headers=[],
            rows=[],
            html="<table><tr><th>Bearer</th></tr><tr><td>b1</td></tr></table>",
            font_info=FontInfo(size=10.0),
        )
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            html_block,
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(foo1.tables) == 1
        assert "<table" in foo1.text


class TestHeadersOnlyTableInline:
    # Headers-only tables (strand table-fidelity): a TABLE block with header
    # cells but no body rows — typically the 1x1 Word paragraph-wrapper
    # pattern — must still land its cell content in the text. The renderer
    # collapses it to the compact "[Table: …]" line.

    def test_render_headers_only_compact_line(self):
        from core.src.parser.structural_parser import render_table_markdown

        assert render_table_markdown(["Note"], []) == "[Table: Note]"
        assert render_table_markdown(["h1", "h2"], []) == "[Table: h1 | h2]"

    def test_render_blank_headers_no_rows_empty(self):
        from core.src.parser.structural_parser import render_table_markdown

        assert render_table_markdown(["", "  "], []) == ""
        assert render_table_markdown([], []) == ""

    def test_render_with_rows_unchanged(self):
        from core.src.parser.structural_parser import render_table_markdown

        out = render_table_markdown(["H"], [["v"]])
        assert out.splitlines() == ["| H |", "| --- |", "| v |"]

    def test_headers_only_table_inlines_and_stores(self):
        blocks = [
            _heading(0, "1 General"),
            _heading(1, "1.1 Bearer Requirements"),
            _body(2, "ABC-FOO-001 The device shall support bearers."),
            _table(3, ["Wrapped paragraph content"], []),
        ]
        tree = _parse(blocks)
        foo1 = next(r for r in tree.requirements if r.req_id == "ABC-FOO-001")
        assert len(foo1.tables) == 1
        assert "[Table: Wrapped paragraph content]" in foo1.text
