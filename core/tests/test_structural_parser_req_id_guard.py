"""Tests for the req_id over-capture guard (strand id-precision).

A permissive plan-token class (spaces allowed, unbounded) under a
one-sided anchor can run past the real id into surrounding prose — any
prose ending in ``-<digits>`` completes the match — and whitespace
canonicalization then welds the capture into one silently corrupted
token. Field-validated on a served corpus (354 corrupted rows under a
leading anchor; 1 per cell under the end-anchored fused-heading
rescue). The guard is the core-side backstop: recovery when the true
id is present at the capture's anchored edge (the weld signature), a
loud no-id skip otherwise — never a silent weld.
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
from core.src.parser.structural_parser import (
    GenericStructuralParser,
    RequirementTree,
)
from core.src.profiler.profile_schema import (
    BodyText,
    CrossReferencePatterns,
    DocumentProfile,
    HeaderFooter,
    HeadingDetection,
    PlanMetadata,
    RequirementIdPattern,
)

# The hazard class: space-bearing, unbounded plan token (mirrors the
# generic <PLAN> expansion), so multi-word plan tokens are matchable —
# and so is a weld.
_PERMISSIVE_PATTERN = r"XPRE-[A-Za-z0-9_ -]+-\d+"


def _profile(
    *, anchor: str = "last_run", detection_mode: str = "heading"
) -> DocumentProfile:
    return DocumentProfile(
        profile_name="test",
        profile_version=1,
        created_from=[],
        last_updated="2026-08-17",
        heading_detection=HeadingDetection(
            method="numbering",
            numbering_pattern=r"^(\d+(?:\.\d+)*)\s+\S",
            max_observed_depth=4,
        ),
        requirement_id=RequirementIdPattern(
            pattern=_PERMISSIVE_PATTERN,
            anchor=anchor,
            detection_mode=detection_mode,
        ),
        plan_metadata=PlanMetadata(),
        document_zones=[],
        header_footer=HeaderFooter(),
        cross_reference_patterns=CrossReferencePatterns(),
        body_text=BodyText(font_size_min=11.0, font_size_max=12.0),
    )


def _parser(**kw) -> GenericStructuralParser:
    return GenericStructuralParser(_profile(**kw))


def _heading(idx: int, text: str, runs: list[str] | None = None) -> ContentBlock:
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=14.0, bold=True),
        runs=[TextRun(text=t, struck=False) for t in runs] if runs else [],
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
        source_file="fixture.pdf",
        source_format="pdf",
        mno="MNO0",
        release="r1",
        doc_type="requirement",
        content_blocks=blocks,
    )


def _parse(profile: DocumentProfile, blocks: list[ContentBlock]) -> RequirementTree:
    return GenericStructuralParser(profile).parse(_doc(blocks))


# ---------------------------------------------------------------------------
# _guard_req_id unit behavior
# ---------------------------------------------------------------------------


class TestGuardUnit:
    def test_clean_id_passes_untouched(self):
        p = _parser()
        assert p._guard_req_id("XPRE-PLAN-1") == "XPRE-PLAN-1"
        assert p._parse_stats.req_id_over_captures_recovered == 0
        assert p._parse_stats.req_id_captures_rejected == 0

    def test_weld_recovered_from_start_side(self):
        # The field weld shape: true id + prose ending in -<digits>.
        raw = "XPRE-PLAN-1 The device shall support handover-2"
        p = _parser()
        assert p._guard_req_id(raw) == "XPRE-PLAN-1"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_legit_multiword_plan_passes(self):
        # Multi-word plan tokens are real corpus shapes — no recovery
        # candidate fullmatches (no interior -<digits> boundary), and the
        # capture is within the word bound.
        p = _parser()
        assert p._guard_req_id("XPRE-Voice WiFi-123") == "XPRE-Voice WiFi-123"
        assert p._parse_stats.req_id_over_captures_recovered == 0
        assert p._parse_stats.req_id_captures_rejected == 0

    def test_end_side_recovery_prefers_trailing_id(self):
        # End-anchored capture spanning a mid-text citation: the TRUE id
        # is the trailing one; prefix recovery would resurrect the
        # citation.
        raw = "XPRE-CITE-1 something in prose XPRE-FOO-123"
        p = _parser()
        assert p._guard_req_id(raw, side="end") == "XPRE-FOO-123"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_unrecoverable_overwide_capture_rejected(self):
        # No whitespace-bounded slice is itself an id and the capture
        # exceeds the word bound → loud skip, not a weld.
        raw = "XPRE-alpha beta gamma delta epsilon-5"
        p = _parser()
        assert p._guard_req_id(raw) == ""
        assert p._parse_stats.req_id_captures_rejected == 1

    def test_overlong_whitespace_free_capture_rejected(self):
        p = _parser()
        raw = "XPRE-" + "A" * 70 + "-1"
        assert p._guard_req_id(raw) == ""
        assert p._parse_stats.req_id_captures_rejected == 1


# ---------------------------------------------------------------------------
# Capture-seam integration
# ---------------------------------------------------------------------------


class TestSeamIntegration:
    def test_fused_single_run_heading_recovers_true_id(self):
        # ``concatenated_run_heading`` rescue (anchor=last_run, one run):
        # findall over text over-captures; the guard recovers the id.
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, "1.1 XPRE-PLAN-1 statement prose ending in tail-9",
                     runs=["1.1 XPRE-PLAN-1 statement prose ending in tail-9"]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-PLAN-1"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_trailing_rescue_recovers_trailing_id_not_citation(self):
        # Multi-run fused heading whose text also carries an earlier
        # citation: the end-anchored rescue match spans from the citation
        # to the end; end-side recovery must return the TRAILING id.
        text = "1.2 See XPRE-CITE-1 then TITLE XPRE-FOO-123"
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, text, runs=["1.2 See XPRE-CITE-1 then TITLE XPRE-", "FOO-123"]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-FOO-123"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_leading_text_anchor_recovers_true_id(self):
        # The served-corpus mechanism: start-anchored, no end bound.
        tree = _parse(_profile(anchor="leading_text"), [
            _heading(0, "1.3 XPRE-PLAN-2 device shall do things-7"),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-PLAN-2"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_leading_id_body_mode_recovers_true_id(self):
        # leading_id_body detection: a body paragraph opening with an id
        # followed by prose ending in -<digits> must anchor on the id,
        # not the weld (core-side equivalent of the profile-tier fix).
        prof = _profile(anchor="leading_text", detection_mode="leading_id_body")
        tree = _parse(prof, [
            _heading(0, "1 Chapter"),
            _para(1, "XPRE-PLAN-3 The system shall complete attach in under-4"),
        ])
        ids = {r.req_id for r in tree.requirements}
        assert "XPRE-PLAN-3" in ids
        # The weld (canonicalized with underscores) must not exist.
        assert not any("_" in rid and "under" in rid for rid in ids)

    def test_clean_parse_leaves_counters_zero(self):
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, "1.4 CLEAN TITLE XPRE-FOO-555",
                     runs=["1.4 CLEAN TITLE ", "XPRE-FOO-555"]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-FOO-555"
        assert tree.parse_stats.req_id_over_captures_recovered == 0
        assert tree.parse_stats.req_id_captures_rejected == 0


# ---------------------------------------------------------------------------
# ParseStats serialization round-trip
# ---------------------------------------------------------------------------


class TestStatsRoundTrip:
    def test_guard_counters_survive_save_load(self, tmp_path):
        tree = _parse(_profile(anchor="leading_text"), [
            _heading(0, "1.3 XPRE-PLAN-2 device shall do things-7"),
            _para(1, "Body."),
        ])
        assert tree.parse_stats.req_id_over_captures_recovered >= 1
        path = tmp_path / "tree.json"
        tree.save_json(path)
        loaded = RequirementTree.load_json(path)
        assert (
            loaded.parse_stats.req_id_over_captures_recovered
            == tree.parse_stats.req_id_over_captures_recovered
        )
        assert (
            loaded.parse_stats.req_id_captures_rejected
            == tree.parse_stats.req_id_captures_rejected
        )

    def test_older_tree_without_counters_loads_zero(self, tmp_path):
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, "1.4 TITLE XPRE-FOO-555",
                     runs=["1.4 TITLE ", "XPRE-FOO-555"]),
        ])
        path = tmp_path / "tree.json"
        tree.save_json(path)
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        data["parse_stats"].pop("req_id_over_captures_recovered", None)
        data["parse_stats"].pop("req_id_captures_rejected", None)
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = RequirementTree.load_json(path)
        assert loaded.parse_stats.req_id_over_captures_recovered == 0
        assert loaded.parse_stats.req_id_captures_rejected == 0
