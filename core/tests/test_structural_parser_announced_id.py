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


def _image(idx: int) -> ContentBlock:
    return ContentBlock(
        type=BlockType.IMAGE,
        position=Position(page=1, index=idx),
        image_path="img.png",
        surrounding_text="figure",
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


# ---------------------------------------------------------------------------
# Backward move (msg 0014): announcement CLOSES its requirement
# ---------------------------------------------------------------------------


def test_trailing_announcement_stamps_idless_section():
    # Body-first shape, id-less enclosing section: heading → body →
    # announcement → next heading. The id moves onto the section; the
    # empty spawned node is dropped.
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.3 Device behavior"),
        _para(2, "The device shall do W."),
        _para(3, "(Mandatory) ID: GP-REQ-110"),
        _heading(4, "4.4 Next area ID: GP-SEC-97"),
    ])
    sec = next(r for r in tree.requirements if r.section_number == "4.3")
    assert sec.req_id == "GP-REQ-110" and sec.priority == "MANDATORY"
    assert "shall do W" in sec.text
    assert sum(r.req_id == "GP-REQ-110" for r in tree.requirements) == 1


def test_trailing_announcement_stamps_subnumbered_body_node():
    # The majority shape from validation: enclosing section has its OWN
    # id; the body is a sub-numbered heading-shaped paragraph that mints
    # an id-less node; the announcement follows it, then the next
    # section. The id lands on the sub-numbered node.
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.5 Feature ID: GP-SEC-96"),
        _heading(2, "4.5.1 The device shall do V."),
        _para(3, "(Optional) ID: GP-REQ-111"),
        _heading(4, "4.6 Next ID: GP-SEC-95"),
    ])
    sub = next(r for r in tree.requirements if r.section_number == "4.5.1")
    assert sub.req_id == "GP-REQ-111" and sub.priority == "OPTIONAL"
    assert sum(r.req_id == "GP-REQ-111" for r in tree.requirements) == 1


def test_empty_announcement_after_announced_req_keeps_node():
    # Two consecutive announcements where the second never gets a body:
    # the preceding content node already has its own id, so the id does
    # NOT move backward — the empty node stays (recall preserved).
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.7 Area ID: GP-SEC-94"),
        _para(2, "(Mandatory) ID: GP-REQ-112"),
        _para(3, "Body of the first."),
        _para(4, "(Optional) ID: GP-REQ-113"),
    ])
    r113 = _by_id(tree, "GP-REQ-113")
    assert r113 is not None and r113.text == ""
    assert _by_id(tree, "GP-REQ-112").text != ""


def test_forward_shape_unaffected_by_post_pass():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.8 Area ID: GP-SEC-93"),
        _para(2, "(Mandatory) ID: GP-REQ-114"),
        _para(3, "Forward body."),
    ])
    req = _by_id(tree, "GP-REQ-114")
    assert "Forward body" in req.text and req.section_number == ""


# ---------------------------------------------------------------------------
# Absorbed-statement extraction (msg 0016): the closing announcement's
# statement was merged into the preceding ID-BEARING node's text by the
# no-font-info gate, so the id-less carrier the plain backward move
# looks for never exists. The post-pass splits the trailing
# sub-numbered segment(s) back out into the announced node.
# ---------------------------------------------------------------------------


def test_absorbed_statement_extracted_from_id_bearing_section():
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.2 Feature ID: GP-SEC-91"),
        _para(2, "ID: GP-SEC-91"),                    # leaked label line (field shape)
        _para(3, "4.2.1 The device shall do Z."),     # absorbed statement
        _para(4, "(Mandatory) ID: GP-REQ-116"),       # closing announcement
        _heading(5, "4.3 Next ID: GP-SEC-90"),
    ])
    req = _by_id(tree, "GP-REQ-116")
    assert req is not None
    assert "shall do Z" in req.text
    assert req.section_number == "4.2.1"
    assert req.priority == "MANDATORY"
    sec = _by_id(tree, "GP-SEC-91")
    assert "shall do Z" not in sec.text
    assert "ID: GP-SEC-91" in sec.text  # leaked label stays with its section


def test_chained_closing_announcements_resolve_via_fixpoint():
    # Chained closing shape mis-routes each statement one announcement
    # forward at walk time (statement N+1 lands on announced node N);
    # the fixpoint sweep unwinds the chain completely.
    tree = _parse([
        _heading(0, "5 Requirements", size=9.8),
        _heading(1, "5.1 Area ID: GP-SEC-89"),
        _para(2, "5.1.1 First statement body."),
        _para(3, "(Mandatory) ID: GP-REQ-117"),
        _para(4, "5.1.2 Second statement body."),
        _para(5, "(Optional) ID: GP-REQ-118"),
        _heading(6, "5.2 Next ID: GP-SEC-88"),
    ])
    r117 = _by_id(tree, "GP-REQ-117")
    r118 = _by_id(tree, "GP-REQ-118")
    assert "First statement" in r117.text and r117.section_number == "5.1.1"
    assert "Second statement" in r118.text and r118.section_number == "5.1.2"
    sec = _by_id(tree, "GP-SEC-89")
    assert "statement body" not in sec.text


def test_absorbed_same_number_segment_not_extracted():
    # A demoted duplicate of the section's OWN number is the section's
    # continuation text, not an absorbed statement — extraction must
    # decline and the empty node stays (recall preserved).
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.9 Area ID: GP-SEC-92"),
        _para(2, "4.9 Area continued prose."),
        _para(3, "(Mandatory) ID: GP-REQ-115"),
    ])
    r115 = _by_id(tree, "GP-REQ-115")
    assert r115 is not None and r115.text == ""
    sec = _by_id(tree, "GP-SEC-92")
    assert "continued prose" in sec.text


def test_space_variant_announcement_canonicalizes_clean():
    # Space-variant labeled announcement ("ID: GP-REQ- 120" — space
    # between the final hyphen and the number; systematic source class,
    # msg 0018). With pattern-side tolerance the announcement anchors,
    # and separator-adjacent whitespace absorbs into the separator —
    # canonical id "GP-REQ-120", never "GP-REQ-_120".
    profile = _profile()
    profile.requirement_id = RequirementIdPattern(
        pattern=r"GP-(?:REQ|SEC)-\s?\d+",
        requirement_type_pattern=r"GP-REQ-\d+",
        id_label_pattern=r"ID:\s*(GP-(?:REQ|SEC)-\s?\d+)",
        anchor="leading_text",
    )
    blocks = [
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.1 Feature area ID: GP-SEC-99"),
        _para(2, "(Mandatory) ID: GP-REQ- 120"),
        _para(3, "The device shall do S."),
    ]
    for i, b in enumerate(blocks):
        b.position.index = i
    ir = DocumentIR(source_file="fixture.pdf", source_format="pdf",
                    mno="MNOC", release="Mar2026", content_blocks=blocks)
    tree = GenericStructuralParser(profile).parse(ir)
    req = _by_id(tree, "GP-REQ-120")
    assert req is not None and "shall do S" in req.text
    assert req.priority == "MANDATORY"
    assert _by_id(tree, "GP-REQ-_120") is None


def test_absorbed_statement_claimed_when_node_has_image():
    # msg 0020 item (a): an announcement followed by an image attaches
    # the image to the announced node — but its statement text is still
    # absorbed in the predecessor. Extraction eligibility is text-only,
    # so the node claims its statement AND keeps the image.
    tree = _parse([
        _heading(0, "7 Requirements", size=9.8),
        _heading(1, "7.1 Area ID: GP-SEC-85"),
        _para(2, "7.1.1 The device shall do R."),
        _para(3, "(Mandatory) ID: GP-REQ-121"),
        _image(4),
        _heading(5, "7.2 Next ID: GP-SEC-84"),
    ])
    req = _by_id(tree, "GP-REQ-121")
    assert req is not None and len(req.images) == 1
    assert "shall do R" in req.text and req.section_number == "7.1.1"
    sec = _by_id(tree, "GP-SEC-85")
    assert "shall do R" not in sec.text


def test_plain_move_declines_when_node_has_image():
    # Id-less predecessor + attachment-carrying announced node: the
    # plain backward move would drop the node (and its image) — it must
    # decline, keeping the node (recall + attachment preserved).
    tree = _parse([
        _heading(0, "8 Requirements", size=9.8),
        _heading(1, "8.1 Device behavior"),
        _para(2, "The device shall do T."),
        _para(3, "(Mandatory) ID: GP-REQ-122"),
        _image(4),
    ])
    req = _by_id(tree, "GP-REQ-122")
    assert req is not None and len(req.images) == 1
    sec = next(r for r in tree.requirements if r.section_number == "8.1")
    assert sec.req_id == ""
    assert "shall do T" in sec.text


# ---------------------------------------------------------------------------
# Chained announcement-after-body mechanisms (msg 0022): sibling
# sub-numbers are claimable; the inline-trailing announcement form
# ("<statement> (Marker) ID: <id>" in one paragraph) keeps its own id.
# ---------------------------------------------------------------------------


def _fpara(idx: int, text: str) -> ContentBlock:
    """Body paragraph WITH FontInfo (takes the scavenging body path)."""
    return ContentBlock(
        type=BlockType.PARAGRAPH,
        position=Position(page=1, index=idx),
        text=text,
        font_info=FontInfo(size=6.5),
    )


def test_sibling_subnumber_tail_is_claimable():
    # Chained docs put statement N+1 into node N, so the claimable tail
    # is a SIBLING of the absorber's number (…6 in a …5 node), not an
    # extension. The absorber here is a minted sub-heading with its own
    # structural id.
    tree = _parse([
        _heading(0, "9 Requirements", size=9.8),
        _heading(1, "9.1 Area ID: GP-SEC-83"),
        _heading(2, "9.1.5 Fifth statement summary ID: GP-SEC-70"),
        _para(3, "9.1.6 Sixth statement text."),
        _para(4, "(Mandatory) ID: GP-REQ-125"),
        _heading(5, "9.2 Next ID: GP-SEC-82"),
    ])
    req = _by_id(tree, "GP-REQ-125")
    assert req is not None
    assert "Sixth statement" in req.text and req.section_number == "9.1.6"
    absorber = _by_id(tree, "GP-SEC-70")
    assert "Sixth statement" not in absorber.text


def test_same_number_and_ancestor_tails_still_refused():
    # The relaxation is siblings-only: a duplicate of the absorber's own
    # number stays its continuation text (regression guard for the
    # demoted-duplicate shape).
    tree = _parse([
        _heading(0, "4 Requirements", size=9.8),
        _heading(1, "4.9 Area ID: GP-SEC-92"),
        _para(2, "4.9 Area continued prose."),
        _para(3, "(Mandatory) ID: GP-REQ-115"),
    ])
    r115 = _by_id(tree, "GP-REQ-115")
    assert r115 is not None and r115.text == ""


def test_inline_trailing_announcement_keeps_own_id():
    # The doc's third announcement form: statement + "(Marker) ID: <id>"
    # in ONE paragraph. The open announced cursor must NOT swallow it
    # (the scavenge would never run and the inline id would be displaced
    # by a neighboring announcement — the field −1 regression). The
    # cursor closes, the block takes the section path, and the scavenge
    # anchors the inline id.
    long_stmt = (
        "9.3.6 The device shall support the sixth capability under all "
        "operating conditions including roaming, and shall report the "
        "corresponding status to the management server whenever the "
        "configuration changes or the reporting interval elapses, per "
        "the referenced management specification."
    )
    assert len(long_stmt) > 200  # stays a body paragraph, not a heading
    tree = _parse([
        _heading(0, "9 Requirements", size=9.8),
        _heading(1, "9.3 Area ID: GP-SEC-81"),
        _heading(2, "9.3.5 Fifth statement summary"),
        _para(3, "(Mandatory) ID: GP-REQ-126"),
        _fpara(4, long_stmt + " (Optional) ID: GP-REQ-127"),
        _heading(5, "9.4 Next ID: GP-SEC-80"),
    ])
    r127 = _by_id(tree, "GP-REQ-127")
    assert r127 is not None                      # inline id stays anchored
    r126 = _by_id(tree, "GP-REQ-126")
    assert r126 is not None                      # announcement id not displaced
    assert "sixth capability" not in (r126.text or "")


def test_plain_move_refuses_trailing_labeled_carrier():
    # An id-less carrier whose text ENDS with a labeled id owns that id;
    # a neighboring announcement must not stamp over it.
    tree = _parse([
        _heading(0, "9 Requirements", size=9.8),
        _heading(1, "9.5 Area"),
        _para(2, "Statement text. (Optional) ID: GP-REQ-128"),
        _para(3, "(Mandatory) ID: GP-REQ-129"),
    ])
    r129 = _by_id(tree, "GP-REQ-129")
    assert r129 is not None and r129.text == ""  # kept, not moved
    sec = next(r for r in tree.requirements if r.section_number == "9.5")
    assert sec.req_id != "GP-REQ-129"


def test_multiline_absorbed_statement_moves_with_continuation():
    # Continuation lines after the sub-numbered opener belong to the
    # statement and move with it.
    tree = _parse([
        _heading(0, "6 Requirements", size=9.8),
        _heading(1, "6.1 Area ID: GP-SEC-87"),
        _para(2, "6.1.1 The device shall do Q"),
        _para(3, "under all operating conditions."),
        _para(4, "(Mandatory) ID: GP-REQ-119"),
        _heading(5, "6.2 Next ID: GP-SEC-86"),
    ])
    req = _by_id(tree, "GP-REQ-119")
    assert "shall do Q" in req.text
    assert "operating conditions" in req.text
    sec = _by_id(tree, "GP-SEC-87")
    assert "shall do Q" not in sec.text and "operating" not in sec.text
