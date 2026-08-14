"""Tests for ``RequirementIdPattern.anchor`` extraction modes.

Phase 2 of the generic-rules pivot: the parser can extract a heading's
req_id from ``runs[-1]`` (DOCX-style headings where the trailing run
*is* the requirement id) instead of regex-searching the full text. The
``anchor`` field on ``RequirementIdPattern`` selects the strategy:

  * ``last_run`` — runs-aware extraction (this file's primary subject).
  * ``leading_text`` — first regex match in the heading's live text.
  * ``trailing_text`` (default) — the heading itself is *not* the
    anchor; the req_id arrives via a separate small-font block (the
    OA-style trailing-marker convention). Covered as a regression
    guard.

``normalize="upper"`` is exercised on a mixed-case plan code (e.g.
``VoWiFi``) to confirm canonical uppercasing.
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


# Pattern accepts mixed-case plan token (so VoWiFi is matchable).
_REQ_PATTERN = r"[A-Z]+_REQ_[A-Za-z0-9_]+_\d+"


def _profile(*, anchor: str = "last_run", normalize: str = "none") -> DocumentProfile:
    return DocumentProfile(
        profile_name="test",
        profile_version=1,
        created_from=[],
        last_updated="2026-05-10",
        heading_detection=HeadingDetection(
            method="numbering",
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
        ),
        requirement_id=RequirementIdPattern(
            pattern=_REQ_PATTERN,
            anchor=anchor,
            normalize=normalize,
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _heading_with_runs(
    idx: int, title_text: str, last_run_text: str
) -> ContentBlock:
    """Heading block where the trailing run holds the req_id."""
    full = f"{title_text}{last_run_text}"
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=full,
        font_info=FontInfo(size=14.0, bold=True),
        runs=[
            TextRun(text=title_text, struck=False),
            TextRun(text=last_run_text, struck=False),
        ],
    )


def _para(idx: int, text: str) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=12.0),
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


def _parse(profile: DocumentProfile, blocks: list[ContentBlock]):
    return GenericStructuralParser(profile).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# anchor="last_run"
# ---------------------------------------------------------------------------


class TestLastRunAnchor:
    def test_extracts_req_id_from_trailing_run(self):
        blocks = [
            _heading_with_runs(0, "1.1 Foo Title ", "VZ_REQ_VOWIFI_1234"),
            _para(1, "body content"),
        ]
        tree = _parse(_profile(anchor="last_run"), blocks)
        assert len(tree.requirements) == 1
        assert tree.requirements[0].req_id == "VZ_REQ_VOWIFI_1234"
        assert tree.requirements[0].section_number == "1.1"
        # Title text is preserved on the heading (currently the full
        # post-numbering text). Phase 2 doesn't reshape title display —
        # that lands when TOC pairing comes online (Phase 3).
        assert tree.requirements[0].title.startswith("Foo Title")

    def test_no_req_id_when_last_run_is_plain_text(self):
        """Last run is title prose — must not be misread as a req_id."""
        blocks = [
            _heading_with_runs(0, "1.1 ", "Foo Title No Marker"),
            _para(1, "body"),
        ]
        tree = _parse(_profile(anchor="last_run"), blocks)
        assert len(tree.requirements) == 1
        assert tree.requirements[0].req_id == ""

    def test_req_id_inline_in_title_is_not_extracted(self):
        """Inline mention of a req_id in the *title* run is not the anchor.

        The whole point of the last-run rule: an inline citation like
        ``"...as defined in VZ_REQ_X_5"`` inside a title should NOT
        promote that to the section's anchor id. Only the trailing run
        in solo-position counts.
        """
        blocks = [
            _heading_with_runs(
                0, "1.1 References to VZ_REQ_X_5 in title ", "Other Tail Text"
            ),
            _para(1, "body"),
        ]
        tree = _parse(_profile(anchor="last_run"), blocks)
        assert len(tree.requirements) == 1
        assert tree.requirements[0].req_id == ""

    def test_falls_back_to_text_when_runs_empty(self):
        """When ``runs=[]`` (DOCX formatting error in source), the
        parser falls back to ``block.text`` for req_id extraction so
        the heading still produces a properly-anchored Requirement
        instead of one with an empty req_id. The format deviation is
        logged separately under ``parser.format_error:
        kind=empty_runs_heading`` for the architect's review."""
        block = ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=0),
            text="1.1 Foo Title VZ_REQ_X_1",
            font_info=FontInfo(size=14.0, bold=True),
        )
        tree = _parse(_profile(anchor="last_run"), [block, _para(1, "body")])
        # Text-fallback extracts the req_id from the trailing token.
        assert tree.requirements[0].req_id == "VZ_REQ_X_1"


# ---------------------------------------------------------------------------
# normalize="upper"
# ---------------------------------------------------------------------------


class TestNormalizeUpper:
    def test_mixed_case_plan_uppercased(self):
        """``VoWiFi`` in the trailing run → canonical ``VOWIFI`` after
        normalize. Solves the work-PC-corpus heading-anchored regression
        flagged 2026-05-09."""
        blocks = [
            _heading_with_runs(0, "1.2 Some Title ", "VZ_REQ_VoWiFi_37621"),
            _para(1, "body"),
        ]
        tree = _parse(
            _profile(anchor="last_run", normalize="upper"), blocks
        )
        assert tree.requirements[0].req_id == "VZ_REQ_VOWIFI_37621"

    def test_normalize_none_preserves_case(self):
        blocks = [
            _heading_with_runs(0, "1.2 Title ", "VZ_REQ_VoWiFi_37621"),
            _para(1, "body"),
        ]
        tree = _parse(
            _profile(anchor="last_run", normalize="none"), blocks
        )
        assert tree.requirements[0].req_id == "VZ_REQ_VoWiFi_37621"


# ---------------------------------------------------------------------------
# anchor="leading_text"
# ---------------------------------------------------------------------------


class TestLeadingTextAnchor:
    def test_extracts_first_match(self):
        block = ContentBlock(
            type=BlockType.PARAGRAPH,
            position=Position(page=1, index=0),
            text="1.1 VZ_REQ_X_1 leading then VZ_REQ_X_2 trailing",
            font_info=FontInfo(size=14.0, bold=True),
        )
        # Numbering pattern requires section-number prefix; place the
        # req_id immediately after to satisfy "leading" semantics.
        block.text = "1.1 VZ_REQ_FOO_1 then later VZ_REQ_FOO_2"
        tree = _parse(_profile(anchor="leading_text"), [block, _para(1, "body")])
        # Leading-text anchor walks the live text after stripping; the
        # earlier match wins.
        assert tree.requirements[0].req_id == "VZ_REQ_FOO_1"


# ---------------------------------------------------------------------------
# anchor="trailing_text" (default) — regression guard
# ---------------------------------------------------------------------------


class TestTrailingTextDefault:
    def test_heading_text_is_not_scanned(self):
        """In the default ``trailing_text`` mode the heading text is
        *not* the req_id source — that's the OA convention where a
        small-font block AFTER the heading carries the marker. An
        inline match in the heading must not be promoted, otherwise
        we'd regress OA parsing."""
        block = _heading_with_runs(
            0, "1.1 Foo Title ", "VZ_REQ_INLINE_99"
        )
        tree = _parse(
            _profile(anchor="trailing_text"), [block, _para(1, "body")]
        )
        # No trailing-marker small-font block follows → req_id stays empty.
        assert tree.requirements[0].req_id == ""


# ---------------------------------------------------------------------------
# Fused trailing id — multi-run rescue (strand req-recall)
# ---------------------------------------------------------------------------
#
# DOCX sources whose heading title and id are separated only at layout
# level (tab stops, run topology) join into ``... TITLEXR-FOO-123`` with a
# run split that defeats the last-run solo-match (id split across runs, or
# a trailing whitespace run). An END-anchored match in the text promotes;
# a mid-heading citation still doesn't.


def _fused_profile() -> DocumentProfile:
    prof = _profile(anchor="last_run")
    prof.requirement_id.pattern = r"XR-[A-Z]{3}-\d+"
    return prof


def _heading_runs(idx: int, text: str, run_texts: list[str]) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
        runs=[TextRun(text=t, struck=False) for t in run_texts],
    )


class TestFusedTrailingHeading:
    def test_fused_id_split_across_runs_rescued(self):
        # id fused to the title in text AND split across runs — no run
        # solo-matches, previously unrecoverable (multi-run no-promotion).
        tree = _parse(_fused_profile(), [
            _heading_runs(0, "1.1 SOME TITLEXR-FOO-123",
                          ["1.1 SOME TITLE", "XR-FOO-", "123"]),
            _para(1, "Body text."),
        ])
        req = tree.requirements[0]
        assert req.req_id == "XR-FOO-123"
        assert "XR-FOO-123" not in req.title      # trailing id stripped from title

    def test_trailing_whitespace_run_rescued(self):
        tree = _parse(_fused_profile(), [
            _heading_runs(0, "1.2 TITLE XR-FOO-777 ",
                          ["1.2 TITLE ", "XR-FOO-777", " "]),
        ])
        assert tree.requirements[0].req_id == "XR-FOO-777"

    def test_midtext_citation_still_not_promoted(self):
        # The no-promotion semantic survives: an id cited mid-heading in a
        # multi-run block is a reference, not the section's anchor.
        tree = _parse(_fused_profile(), [
            _heading_runs(0, "1.3 See XR-FOO-999 for detail",
                          ["1.3 See ", "XR-FOO-999", " for detail"]),
        ])
        assert tree.requirements[0].req_id == ""

    def test_clean_last_run_path_unchanged(self):
        tree = _parse(_fused_profile(), [
            _heading_runs(0, "1.4 CLEAN TITLE XR-FOO-555",
                          ["1.4 CLEAN TITLE ", "XR-FOO-555"]),
        ])
        assert tree.requirements[0].req_id == "XR-FOO-555"
