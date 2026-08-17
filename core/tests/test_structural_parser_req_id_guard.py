"""Tests for the req_id over-capture guard (strand id-precision).

A permissive plan-token class (spaces allowed, unbounded) under a
one-sided anchor can run past the real id into surrounding prose — any
prose ending in ``-<digits>`` completes the match — and whitespace
canonicalization then welds the capture into one silently corrupted
token. Field-validated on a served corpus (354 corrupted rows under a
leading anchor; 1 per cell under the end-anchored fused-heading
rescue).

BOUNDS-FIRST semantics (field-corrected): in-bound captures pass
UNTOUCHED — the first deployment recovered/rejected on every
whitespace-bearing capture with a word bound of 3, which truncated
legitimate multi-word ids (plan tokens carry interior ``-<digits>``
segments) and rejected ~220 legitimate ids per cell on a corpus whose
plan-token inventory reaches 5 words. Recovery and rejection now engage
only past the profile-tunable bound (``requirement_id.guard_max_words``,
default 6).
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
    guard_req_id_capture,
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
    *,
    anchor: str = "last_run",
    detection_mode: str = "heading",
    guard_max_words: int = 6,
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
            guard_max_words=guard_max_words,
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


# A weld comfortably past the default bound: true id + 8 words of prose
# ending in -<digits>.
_LONG_WELD = "XPRE-PLAN-1 The device shall keep running text until tail-2"


# ---------------------------------------------------------------------------
# _guard_req_id unit behavior (bounds-first)
# ---------------------------------------------------------------------------


class TestGuardUnit:
    def test_clean_id_passes_untouched(self):
        p = _parser()
        assert p._guard_req_id("XPRE-PLAN-1") == "XPRE-PLAN-1"
        assert p._parse_stats.req_id_over_captures_recovered == 0
        assert p._parse_stats.req_id_captures_rejected == 0

    def test_weld_recovered_from_start_side(self):
        p = _parser()
        assert p._guard_req_id(_LONG_WELD) == "XPRE-PLAN-1"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_legit_multiword_plan_passes(self):
        p = _parser()
        assert p._guard_req_id("XPRE-Voice WiFi-123") == "XPRE-Voice WiFi-123"
        assert p._parse_stats.req_id_over_captures_recovered == 0
        assert p._parse_stats.req_id_captures_rejected == 0

    def test_five_word_inventory_passes(self):
        # Field-validated inventory shape: 5-word plan token = 5 raw words
        # (first fuses with the prefix, last with the number). The first
        # deployment's bound of 3 rejected this whole family.
        p = _parser()
        raw = "XPRE-Ultra Wide Band Pro Max-99"
        assert p._guard_req_id(raw) == raw
        assert p._parse_stats.req_id_captures_rejected == 0

    def test_inbound_id_with_fullmatching_prefix_is_never_truncated(self):
        # Field-confirmed hazard of the first deployment: a legitimate
        # plan token with an interior -<digits> segment has a prefix
        # slice that fullmatches the pattern. An in-bound capture must
        # pass UNTOUCHED — recovering here silently replaces a real id
        # with its truncation.
        p = _parser()
        raw = "XPRE-Rel-15 NR Support-123"
        assert p._guard_req_id(raw) == raw
        assert p._parse_stats.req_id_over_captures_recovered == 0

    def test_end_side_recovery_prefers_trailing_id(self):
        # Over-bound end-anchored capture spanning a mid-text citation:
        # the TRUE id is the trailing one; prefix recovery would
        # resurrect the citation.
        raw = "XPRE-CITE-1 some longer prose keeps running here XPRE-FOO-123"
        p = _parser()
        assert p._guard_req_id(raw, side="end") == "XPRE-FOO-123"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_unrecoverable_overwide_capture_rejected(self):
        raw = "XPRE-alpha beta gamma delta epsilon zeta eta theta-5"
        p = _parser()
        assert p._guard_req_id(raw) == ""
        assert p._parse_stats.req_id_captures_rejected == 1

    def test_overlong_whitespace_free_capture_rejected(self):
        p = _parser()
        raw = "XPRE-" + "A" * 70 + "-1"
        assert p._guard_req_id(raw) == ""
        assert p._parse_stats.req_id_captures_rejected == 1

    def test_containment_catches_inbound_weld(self):
        # Field experiment: the id+prose+id weld can be NARROWER than the
        # widest legitimate id, so no word bound separates them. Both
        # edges fullmatching = two complete ids in one capture → recover
        # from the anchored side, bounds notwithstanding.
        raw = "XPRE-PLAN-1 word XPRE-FOO-2"    # 3 words, well in-bound
        p = _parser()
        assert p._guard_req_id(raw) == "XPRE-PLAN-1"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_containment_end_side_returns_trailing_id(self):
        raw = "XPRE-PLAN-1 word XPRE-FOO-2"
        p = _parser()
        assert p._guard_req_id(raw, side="end") == "XPRE-FOO-2"

    def test_containment_adjacent_ids_recover(self):
        p = _parser()
        assert p._guard_req_id("XPRE-A-1 XPRE-B-2") == "XPRE-A-1"
        assert p._parse_stats.req_id_over_captures_recovered == 1

    def test_single_id_prose_tail_inbound_still_passes(self):
        # The complementary weld shape (id + prose, ONE complete id):
        # in-bound it passes — that class stays governed by the word
        # bound and profile class discipline, per the field record.
        p = _parser()
        raw = "XPRE-PLAN-1 short tail-here"
        assert p._guard_req_id(raw) == raw
        assert p._parse_stats.req_id_over_captures_recovered == 0

    def test_profile_knob_tightens_the_bound(self):
        # guard_max_words is corpus inventory: with a bound of 2, a
        # 3-word capture is over-bound; no slice fullmatches → reject.
        p = _parser(guard_max_words=2)
        assert p._guard_req_id("XPRE-Ultra Wide Band-99") == ""
        assert p._parse_stats.req_id_captures_rejected == 1

    def test_recovered_slice_must_be_inbound(self):
        # Recovery honors the same bound: when the only fullmatching
        # slice is itself over-bound (2 words vs max_words=1), it is not
        # recovered — the capture rejects.
        import re as _re
        raw = "XPRE-Voice WiFi-123 with extra prose tail running long-7"
        guarded, action = guard_req_id_capture(
            raw, _re.compile(_PERMISSIVE_PATTERN), max_words=1,
        )
        assert (guarded, action) == ("", "rejected")


# ---------------------------------------------------------------------------
# Capture-seam integration
# ---------------------------------------------------------------------------


class TestSeamIntegration:
    def test_fused_single_run_heading_recovers_true_id(self):
        # ``concatenated_run_heading`` rescue (anchor=last_run, one run):
        # findall over text over-captures; the guard recovers the id.
        text = "1.1 " + _LONG_WELD
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, text, runs=[text]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-PLAN-1"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_trailing_rescue_recovers_trailing_id_not_citation(self):
        # Multi-run fused heading whose text also carries an earlier
        # citation: the end-anchored rescue match spans from the citation
        # to the end; end-side recovery must return the TRAILING id.
        text = "1.2 See XPRE-CITE-1 then a longer running title XPRE-FOO-123"
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, text,
                     runs=["1.2 See XPRE-CITE-1 then a longer running title XPRE-",
                           "FOO-123"]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-FOO-123"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_leading_text_anchor_recovers_true_id(self):
        # The served-corpus mechanism: start-anchored, no end bound.
        tree = _parse(_profile(anchor="leading_text"), [
            _heading(0, "1.3 " + _LONG_WELD),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-PLAN-1"
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_leading_id_body_mode_recovers_true_id(self):
        # leading_id_body detection: a body paragraph opening with an id
        # followed by prose ending in -<digits> must anchor on the id,
        # not the weld (core-side equivalent of the profile-tier fix).
        prof = _profile(anchor="leading_text", detection_mode="leading_id_body")
        tree = _parse(prof, [
            _heading(0, "1 Chapter"),
            _para(1, "XPRE-PLAN-3 The system shall always complete attach "
                     "procedures with time to spare under-4"),
        ])
        ids = {r.req_id for r in tree.requirements}
        assert "XPRE-PLAN-3" in ids
        # The weld (canonicalized with underscores) must not exist.
        assert not any("_" in rid and "under" in rid for rid in ids)

    def test_fused_heading_inbound_weld_caught_by_containment(self):
        # The field weld shape: a fused single-run heading whose capture
        # is id + short prose + id — inside the word envelope, invisible
        # to any bound; containment recovers the true id.
        text = "1.6 XPRE-PLAN-1 word XPRE-FOO-2"
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, text, runs=[text]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id in ("XPRE-PLAN-1", "XPRE-FOO-2")
        assert "word" not in tree.requirements[0].req_id
        assert tree.parse_stats.req_id_over_captures_recovered >= 1

    def test_multiword_id_survives_full_parse_untouched(self):
        # A clean 5-word-plan id through the solo-run last_run path:
        # in-bound → no recovery, no rejection, canonical underscores.
        tree = _parse(_profile(anchor="last_run"), [
            _heading(0, "1.5 TITLE XPRE-Ultra Wide Band Pro Max-99",
                     runs=["1.5 TITLE ", "XPRE-Ultra Wide Band Pro Max-99"]),
            _para(1, "Body."),
        ])
        assert tree.requirements[0].req_id == "XPRE-Ultra_Wide_Band_Pro_Max-99"
        assert tree.parse_stats.req_id_over_captures_recovered == 0
        assert tree.parse_stats.req_id_captures_rejected == 0

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
            _heading(0, "1.3 " + _LONG_WELD),
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
