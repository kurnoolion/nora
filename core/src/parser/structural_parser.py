"""Generic, profile-driven structural parser (TDD 5.3).

Parses normalized IR + document profile into a structured requirement
tree. No per-MNO code — behavior driven entirely by the profile JSON.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from core.src.models.document import BlockType, ContentBlock, DocumentIR
from core.src.profiler.profile_schema import DocumentProfile, HeadingLevel

logger = logging.getLogger(__name__)


# Maximum heading text length. Headings in technical specs are usually short;
# numbered body sentences ("1. The system shall ...") run longer. 200 chars
# accommodates verbose spec titles like
# "1.2.3.4 LTE Idle Mode Procedures and Behavior Under Various Conditions"
# while still rejecting body-paragraph numbered list items.
_HEADING_MAX_LEN = 200


# Section number extractor — independent of whatever capture-group shape the
# profile's `numbering_pattern` happens to use. We use the profile pattern as
# a gate ("is this a heading?") and this regex to pull out the canonical
# `<digits>(\.<digits>)*` portion.
_SECTION_NUM_RE = re.compile(r"^\d+(?:\.\d+)*")


# Req-ID canonicalization. PDF text extraction occasionally drops the
# underscore between a req_id's plan-prefix and trailing digit (e.g.
# `"VZ_REQ_LTEOTADM_65"` → extracted as `"VZ_REQ_LTEOTADM 65"` because
# bold-formatting fused two text runs). Profile patterns now accept
# either separator; this helper normalizes a matched id to the
# canonical underscore form for storage and comparison so the same
# requirement isn't tracked under two different identifiers.
_REQ_ID_WHITESPACE_RE = re.compile(r"\s+")


def _canonicalize_req_id(rid: str) -> str:
    """Normalize whitespace in a matched req_id to underscores."""
    return _REQ_ID_WHITESPACE_RE.sub("_", rid).strip("_")


# ── Output data structures ──────────────────────────────────────────


@dataclass
class TableData:
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    source: str = "inline"  # "inline" or "embedded_xlsx"


@dataclass
class ImageRef:
    path: str = ""
    surrounding_text: str = ""


@dataclass
class StandardsRef:
    spec: str = ""
    section: str = ""
    release: str = ""


@dataclass
class CrossReferences:
    internal: list[str] = field(default_factory=list)  # req IDs within same plan
    external_plans: list[str] = field(default_factory=list)  # other plan names
    standards: list[StandardsRef] = field(default_factory=list)


@dataclass
class Requirement:
    req_id: str = ""
    section_number: str = ""
    title: str = ""
    parent_req_id: str = ""
    parent_section: str = ""
    hierarchy_path: list[str] = field(default_factory=list)
    zone_type: str = ""
    priority: str = ""  # FR-31: extracted via profile.heading_detection.priority_marker_pattern
    applicability: list[str] = field(default_factory=list)  # FR-32 [D-030]: form-factor labels
    text: str = ""
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # child req_ids
    cross_references: CrossReferences = field(default_factory=CrossReferences)


@dataclass
class ParseStats:
    """Per-document parser diagnostics. Surfaced in compact RPT.

    Counters are zero by default; populated by the parser passes that
    drop or extract content. Consumers should not rely on field presence
    on serialized older trees — `RequirementTree._from_dict` defaults
    missing values to zero.
    """
    struck_blocks_dropped: int = 0  # FR-33 [D-031]
    toc_blocks_dropped: int = 0     # FR-34
    revhist_blocks_dropped: int = 0 # FR-34 (revision-history table omission)
    defs_extracted: int = 0         # FR-35 [D-032]


@dataclass
class RequirementTree:
    mno: str = ""
    release: str = ""
    plan_id: str = ""
    plan_name: str = ""
    version: str = ""
    release_date: str = ""
    referenced_standards_releases: dict[str, str] = field(default_factory=dict)
    requirements: list[Requirement] = field(default_factory=list)
    parse_stats: ParseStats = field(default_factory=ParseStats)
    definitions_map: dict[str, str] = field(default_factory=dict)
    """FR-35 [D-032]: term → expansion pairs extracted from the document's
    definitions / acronyms / glossary section. Per-document scope (not
    aggregated across the corpus) so a term that means different things
    in different MNO documents doesn't collide. Consumed at chunk-build
    time by the vectorstore stage."""
    definitions_section_number: str = ""
    """Section number of the definitions / acronyms / glossary section
    when one was identified (else empty). The chunk builder uses it to
    skip inline expansion within the section's own chunks (and its
    descendants), avoiding `ETWS (Earthquake...) — Earthquake...`-style
    double-anchoring."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> RequirementTree:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        reqs = []
        for r in data.get("requirements", []):
            xr = r.get("cross_references", {})
            standards = [StandardsRef(**s) for s in xr.get("standards", [])]
            reqs.append(Requirement(
                req_id=r.get("req_id", ""),
                section_number=r.get("section_number", ""),
                title=r.get("title", ""),
                parent_req_id=r.get("parent_req_id", ""),
                parent_section=r.get("parent_section", ""),
                hierarchy_path=r.get("hierarchy_path", []),
                zone_type=r.get("zone_type", ""),
                priority=r.get("priority", ""),
                applicability=r.get("applicability", []),
                text=r.get("text", ""),
                tables=[TableData(**t) for t in r.get("tables", [])],
                images=[ImageRef(**i) for i in r.get("images", [])],
                children=r.get("children", []),
                cross_references=CrossReferences(
                    internal=xr.get("internal", []),
                    external_plans=xr.get("external_plans", []),
                    standards=standards,
                ),
            ))
        ps = data.get("parse_stats", {}) or {}
        return cls(
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            plan_id=data.get("plan_id", ""),
            plan_name=data.get("plan_name", ""),
            version=data.get("version", ""),
            release_date=data.get("release_date", ""),
            referenced_standards_releases=data.get("referenced_standards_releases", {}),
            requirements=reqs,
            parse_stats=ParseStats(
                struck_blocks_dropped=ps.get("struck_blocks_dropped", 0),
                toc_blocks_dropped=ps.get("toc_blocks_dropped", 0),
                revhist_blocks_dropped=ps.get("revhist_blocks_dropped", 0),
                defs_extracted=ps.get("defs_extracted", 0),
            ),
            definitions_map=dict(data.get("definitions_map", {})),
            definitions_section_number=data.get("definitions_section_number", ""),
        )


# ── Parser ──────────────────────────────────────────────────────────


class GenericStructuralParser:
    """Profile-driven structural parser for requirement documents."""

    def __init__(self, profile: DocumentProfile):
        self.profile = profile
        # Pre-compile regexes from profile
        self._num_re = (
            re.compile(profile.heading_detection.numbering_pattern)
            if profile.heading_detection.numbering_pattern
            else None
        )
        self._req_id_re = (
            re.compile(profile.requirement_id.pattern)
            if profile.requirement_id.pattern
            else None
        )
        # Revision/version-history heading detection (FR-34) — compiled
        # once; None if disabled. Drops the matching paragraph and the
        # immediately-following table block (within a small window).
        self._revhist_re = (
            re.compile(profile.revision_history_heading_pattern)
            if profile.revision_history_heading_pattern
            else None
        )
        # TOC entry detection (FR-34) — compiled once; None if disabled
        self._toc_re = (
            re.compile(profile.toc_detection_pattern)
            if profile.toc_detection_pattern
            else None
        )
        # Priority marker detection (FR-31) — compiled once; None if disabled
        self._priority_re = (
            re.compile(profile.heading_detection.priority_marker_pattern)
            if profile.heading_detection.priority_marker_pattern
            else None
        )
        # Applicability detection (FR-32 [D-030]) — compiled once
        ad = profile.applicability_detection
        self._applicability_res = [re.compile(p) for p in ad.requirement_patterns]
        self._applicability_global_re = (
            re.compile(ad.global_section_pattern) if ad.global_section_pattern else None
        )
        self._applicability_split_re = (
            re.compile(ad.label_split_pattern, re.IGNORECASE)
            if ad.label_split_pattern
            else None
        )
        # Definitions / acronyms detection (FR-35 [D-032]) — compiled once
        self._definitions_section_re = (
            re.compile(profile.heading_detection.definitions_section_pattern)
            if profile.heading_detection.definitions_section_pattern
            else None
        )
        self._definitions_entry_re = (
            re.compile(profile.definitions_entry_pattern, re.MULTILINE)
            if profile.definitions_entry_pattern
            else None
        )
        # Cross-reference regexes
        self._std_res = [
            re.compile(p)
            for p in profile.cross_reference_patterns.standards_citations
        ]
        self._section_ref_re = (
            re.compile(profile.cross_reference_patterns.internal_section_refs)
            if profile.cross_reference_patterns.internal_section_refs
            else None
        )
        # 3GPP spec + section extraction (more specific than profile patterns)
        # Use \d[\d.]*\d to avoid capturing trailing dots from punctuation
        self._std_detail_re = re.compile(
            r"3GPP\s+TS\s+(\d[\d.]*\d)\s+(?:[Ss]ection\s+)?(\d[\d.]*\d)"
        )
        self._std_release_re = re.compile(
            r"3GPP\s+(?:TS\s+\d[\d.]*\d\s+)?[Rr]elease\s+(\d+)"
        )
        # Plan ID extraction from requirement IDs (profile-driven)
        components = profile.requirement_id.components
        self._req_id_separator = components.get("separator", "_")
        self._req_id_plan_pos = components.get("plan_id_position")
        # Zone classification
        self._zone_map = {
            z.section_pattern: z.zone_type
            for z in profile.document_zones
        }

    def parse(self, doc: DocumentIR) -> RequirementTree:
        """Parse a document IR into a structured requirement tree."""
        logger.info(f"Parsing {doc.source_file} with profile '{self.profile.profile_name}'")

        # Per-document parse diagnostics — populated by passes that drop or
        # extract content. Surfaced in the compact RPT.
        self._parse_stats = ParseStats()

        # 1. Extract plan metadata
        plan_meta = self._extract_plan_metadata(doc)

        # 2. Classify blocks and build section hierarchy.
        #    Two anchor sources for Requirements (see Key Choices in MODULE.md):
        #    paragraph anchors (heading or standalone-ID-in-small-font) and
        #    table-cell anchors (req-IDs found in column-1 of a row, falling
        #    back to all cells). Paragraph anchors win on duplicate req_ids.
        sections = self._build_sections(doc)

        # 3. Extract referenced standards releases
        std_releases = self._extract_standards_releases(doc)

        # 4. Extract cross-references for each section
        plan_id = plan_meta.get("plan_id", "")
        for sec in sections:
            sec.cross_references = self._extract_cross_refs(sec.text, plan_id)

        # 5. Build parent-child relationships
        self._link_parents(sections)

        # 6. Table-anchored Requirements have no section_number, so
        #    _link_parents skips them. Inherit hierarchy_path from their
        #    paragraph-anchored parent now that the parents are linked.
        self._propagate_hierarchy_to_table_reqs(sections)

        # 7. Apply form-factor applicability with hierarchical inheritance
        #    (FR-32 [D-030]). Document-order walk; explicit value wins,
        #    else inherit from parent_section, else fall back to root default.
        self._apply_applicability(sections)

        # 8. Extract definitions / acronyms map from the glossary section
        #    (FR-35 [D-032]). The section itself stays in the parsed tree;
        #    the map is consumed at chunk-build time by the vectorstore.
        definitions_map, definitions_section_number = self._extract_definitions(sections)
        self._parse_stats.defs_extracted = len(definitions_map)

        tree = RequirementTree(
            mno=doc.mno,
            release=doc.release,
            plan_id=plan_meta.get("plan_id", ""),
            plan_name=plan_meta.get("plan_name", ""),
            version=plan_meta.get("version", ""),
            release_date=plan_meta.get("release_date", ""),
            referenced_standards_releases=std_releases,
            requirements=sections,
            parse_stats=self._parse_stats,
            definitions_map=definitions_map,
            definitions_section_number=definitions_section_number,
        )

        logger.info(
            f"Parsed {doc.source_file}: {len(sections)} requirements, "
            f"plan_id={tree.plan_id}"
        )
        return tree

    # ── Plan metadata ───────────────────────────────────────────────

    def _extract_plan_metadata(self, doc: DocumentIR) -> dict[str, str]:
        """Extract plan-level metadata using profile patterns."""
        first_page_text = " ".join(
            b.text
            for b in doc.blocks_by_type(BlockType.PARAGRAPH)
            if b.position.page == 1
        )

        meta = {}
        for field_name in ["plan_name", "plan_id", "version", "release_date"]:
            mf = getattr(self.profile.plan_metadata, field_name)
            if mf.pattern:
                m = re.search(mf.pattern, first_page_text)
                if m:
                    meta[field_name] = m.group(1).strip()

        logger.info(f"Plan metadata: {meta}")
        return meta

    # ── Section hierarchy ───────────────────────────────────────────

    def _identify_toc_pages(self, doc: DocumentIR) -> set[int]:
        """Return the set of page numbers classified as TOC pages (FR-34).

        A page is a TOC page when at least `toc_page_threshold` of its
        paragraph blocks match `toc_detection_pattern`. Pages with no
        paragraph blocks are never TOC pages. When `_toc_re` is None
        (TOC detection disabled), returns an empty set.
        """
        if self._toc_re is None:
            return set()
        threshold = self.profile.toc_page_threshold
        if threshold <= 0.0 or threshold > 1.0:
            return set()  # disabled / invalid
        # Bucket paragraph blocks by page; count how many match the pattern.
        per_page_total: dict[int, int] = {}
        per_page_match: dict[int, int] = {}
        for b in doc.content_blocks:
            if b.type != BlockType.PARAGRAPH or not b.text:
                continue
            page = b.position.page
            per_page_total[page] = per_page_total.get(page, 0) + 1
            if self._toc_re.search(b.text.strip()):
                per_page_match[page] = per_page_match.get(page, 0) + 1
        toc_pages: set[int] = set()
        for page, total in per_page_total.items():
            if total >= 2 and per_page_match.get(page, 0) / total >= threshold:
                toc_pages.add(page)
        if toc_pages:
            logger.info(
                f"TOC pages detected: {sorted(toc_pages)} "
                f"(threshold={threshold:.0%})"
            )
        return toc_pages

    def _build_sections(self, doc: DocumentIR) -> list[Requirement]:
        """Build the flat list of sections with hierarchy info from content blocks."""
        sections: list[Requirement] = []
        current_section: Requirement | None = None

        # Pending req ID — small font blocks that appear before/after a heading
        pending_req_id: str = ""

        # Track which req_ids were anchored by a paragraph (heading-block
        # assignment, pending-id resolution, or inline body-text id) so the
        # table-anchored detection can dedup against them — paragraph wins.
        paragraph_req_ids: set[str] = set()

        # Track req_ids that appeared in a struck-through block. Table
        # cells containing these ids must NOT produce table-anchored reqs
        # — the strikethrough means the source author marked them deleted.
        # PDF strike detection is geometric (lines crossing text); table
        # cells often slip past it because the cell text and the strike
        # line don't intersect cleanly. Recording the id when we see it
        # struck in a paragraph block is a reliable secondary signal.
        struck_req_ids: set[str] = set()

        # Tables are deferred: collected during the main walk, processed
        # for table-anchored req extraction in a SECOND pass after
        # paragraph_req_ids and struck_req_ids are fully populated.
        # Without deferral, a req_id whose paragraph anchor is later in
        # the document gets duplicated as table-anchored when the table
        # appears earlier (e.g. in a change-log table on page 3 vs the
        # real anchor on page 34).
        deferred_tables: list[tuple[ContentBlock, Requirement]] = []

        # Heading-continuation defense state. PyMuPDF text extraction
        # often splits a long heading across multiple blocks ("1.1.7
        # DEVICE TESTING ON ... BAND" / "13 NETWORK"). When the
        # continuation line happens to start with `<digits><space>
        # <uppercase>`, the relaxed numbering gate misclassifies it as
        # a phantom depth-1 chapter. The fingerprint of these false
        # positives is precise: depth-1 section_number AFTER a deeper
        # section has been classified, with the immediately-preceding
        # block also heading-shaped (no body text intervening). When
        # that fingerprint matches, the new "section" is appended to
        # the current section's title as a continuation rather than
        # creating a phantom chapter.
        seen_deep_section = False
        previous_block_was_heading = False

        # Track section_numbers already created. Numbering-driven heading
        # classification is permissive enough that body paragraphs starting
        # with a previously-seen section number can occasionally match the
        # gate. Section numbers are unique per document, so the first
        # heading wins; later matches with the same number are demoted to
        # body text appended to the current section.
        seen_section_numbers: set[str] = set()

        # FR-34: identify TOC pages — pages where >= toc_page_threshold of
        # paragraph blocks match the TOC entry pattern. Computed once up
        # front so the per-block loop below can drop matching blocks AND
        # all blocks on a TOC page (including non-matching ones, e.g. a
        # "Table of Contents" header).
        toc_pages = self._identify_toc_pages(doc)

        # FR-34: revision-history table omission. When a paragraph
        # matches the revision-history heading pattern, set this to
        # `revhist_window` so the next table block (within the window)
        # gets dropped. Window tolerates one image between the heading
        # and the table (some MNOs place a logo there); decremented per
        # block until it expires or a table is consumed.
        revhist_window = 0
        REVHIST_WINDOW = 3

        def _record_paragraph_anchor(rid: str) -> None:
            if rid:
                paragraph_req_ids.add(rid)

        for block in doc.content_blocks:
            # FR-33 [D-031]: drop struck-through blocks. Checked first so
            # struck content never feeds heading classification, table
            # anchoring, or zone matching. Gated by profile.ignore_strikeout.
            if (
                self.profile.ignore_strikeout
                and block.font_info is not None
                and block.font_info.strikethrough
            ):
                # Before dropping, mine any req_id patterns out of the
                # struck text — those ids are "deleted" and must not
                # surface via table cross-references either.
                if self._req_id_re and block.text:
                    for sid in self._find_req_ids(block.text):
                        struck_req_ids.add(sid)
                self._parse_stats.struck_blocks_dropped += 1
                continue

            # FR-34: drop entire-page TOC content (any block type) and any
            # block that matches the TOC entry pattern.
            if block.position.page in toc_pages:
                self._parse_stats.toc_blocks_dropped += 1
                continue
            if (
                self._toc_re is not None
                and block.type == BlockType.PARAGRAPH
                and block.text
                and self._toc_re.search(block.text.strip())
            ):
                self._parse_stats.toc_blocks_dropped += 1
                continue

            # FR-34: drop the revision-history table when its preceding
            # heading matched. The window persists across iterations
            # because tables are emitted as their own block, so the
            # decision is "does the next table belong to a revhist
            # heading?". A new paragraph closes the window without
            # consuming anything (the heading was a false positive).
            if revhist_window > 0:
                if block.type == BlockType.TABLE:
                    self._parse_stats.revhist_blocks_dropped += 1
                    revhist_window = 0
                    continue
                if block.type == BlockType.PARAGRAPH:
                    revhist_window = 0  # window closes on next paragraph
                else:
                    revhist_window -= 1
                # fall through — the block still gets normal processing

            if (
                self._revhist_re is not None
                and block.type == BlockType.PARAGRAPH
                and block.text
                and self._revhist_re.match(block.text.strip())
            ):
                self._parse_stats.revhist_blocks_dropped += 1
                revhist_window = REVHIST_WINDOW
                continue

            if block.type == BlockType.PARAGRAPH:
                if not block.font_info:
                    if current_section:
                        self._append_text(current_section, block.text)
                    continue

                # Check if this is a requirement ID block (small font)
                if self._is_req_id_block(block):
                    req_ids = self._find_req_ids(block.text)
                    if req_ids:
                        if current_section is None:
                            # No section opened yet — hold for the first heading.
                            # (Rare; only fires when a doc-level id precedes
                            # any structural heading.)
                            pending_req_id = req_ids[0]
                        elif not current_section.req_id:
                            # Trailing-marker pattern (OA): the small-font id
                            # right after a heading is THIS section's id.
                            current_section.req_id = req_ids[0]
                            _record_paragraph_anchor(req_ids[0])
                        else:
                            # Section already has a req_id. Ignore — the first
                            # one wins. Lateralling to the next section's
                            # `pending_req_id` is wrong for trailing-marker
                            # corpora and produces an off-by-one cascade.
                            logger.debug(
                                "Extra req_id %s ignored — section %r already has %r",
                                req_ids[0],
                                current_section.section_number,
                                current_section.req_id,
                            )
                    # The req_id "closes" the current heading group: a
                    # later block, even if heading-shaped, is the start
                    # of a new heading, not a continuation of the prior
                    # one.
                    previous_block_was_heading = False
                    continue

                # Check if this is a heading block
                section_num, heading_text = self._classify_heading(block)
                if section_num:
                    new_depth = section_num.count(".") + 1

                    # Heading-continuation defense. PyMuPDF often splits a
                    # multi-line heading across blocks; when the second
                    # line happens to start with `<digits><space><uppercase>`
                    # (e.g. "13 NETWORK" continuing "1.1.7 ... BAND"),
                    # the relaxed numbering gate misclassifies it as a
                    # phantom depth-1 chapter. Fingerprint:
                    #   - depth-1 section_number
                    #   - we've already created at least one deep section
                    #   - the immediately-preceding block was also
                    #     heading-shaped (no body text intervening — req_id
                    #     blocks reset the flag).
                    # When all three hold, the new "section" is appended
                    # to the current section's title as a continuation.
                    if (
                        new_depth == 1
                        and seen_deep_section
                        and previous_block_was_heading
                        and current_section is not None
                        and current_section.section_number
                    ):
                        cont_text = block.text.strip()
                        if cont_text:
                            current_section.title = (
                                (current_section.title + " " + cont_text).strip()
                                if current_section.title
                                else cont_text
                            )
                        # Stay in heading context — multi-line continuations stack.
                        continue

                    # FR-31: extract priority marker (if any) from heading text
                    # before storing — title carries the cleaned form.
                    priority, heading_text = self._extract_priority(heading_text)
                    if section_num not in seen_section_numbers:
                        # New section — first occurrence wins.
                        current_section = Requirement(
                            section_number=section_num,
                            title=heading_text,
                            req_id=pending_req_id,
                            zone_type=self._classify_zone(section_num),
                            priority=priority,
                        )
                        if pending_req_id:
                            _record_paragraph_anchor(pending_req_id)
                        pending_req_id = ""
                        sections.append(current_section)
                        seen_section_numbers.add(section_num)
                        previous_block_was_heading = True
                        if new_depth >= 2:
                            seen_deep_section = True
                        continue
                    # Duplicate section_number. If the prior occurrence is a
                    # "phantom" — empty req_id AND empty body AND no children
                    # yet — it likely came from a TOC residual or other
                    # noise. Replace it with this real heading rather than
                    # demote the real heading to body text. Preserves the
                    # invariant that section_numbers are unique while
                    # protecting against off-by-one cascades on req_id
                    # assignment when TOC bleed-through creates a phantom.
                    existing = next(
                        (s for s in sections if s.section_number == section_num),
                        None,
                    )
                    if (
                        existing is not None
                        and not existing.req_id
                        and not existing.text
                        and not existing.children
                    ):
                        existing.title = heading_text
                        existing.req_id = pending_req_id
                        existing.zone_type = self._classify_zone(section_num)
                        existing.priority = priority
                        if pending_req_id:
                            _record_paragraph_anchor(pending_req_id)
                        pending_req_id = ""
                        current_section = existing
                        previous_block_was_heading = True
                        if new_depth >= 2:
                            seen_deep_section = True
                        continue
                # Section number duplicate (real, with content) or no match
                # → fall through to body text path.

                # Body text — append to current section
                if current_section:
                    self._append_text(current_section, block.text)
                    # Also check for inline req IDs in body text
                    if self._req_id_re and not current_section.req_id:
                        ids = self._find_req_ids(block.text)
                        if ids:
                            current_section.req_id = ids[0]
                            _record_paragraph_anchor(ids[0])
                # Body text breaks the heading-continuation chain.
                previous_block_was_heading = False

            elif block.type == BlockType.TABLE:
                if current_section:
                    current_section.tables.append(
                        TableData(
                            headers=block.headers,
                            rows=block.rows,
                            source="inline",
                        )
                    )
                    # Defer table-anchored extraction to a second pass —
                    # paragraph_req_ids and struck_req_ids must be
                    # complete before we decide what to anchor (see
                    # comment at deferred_tables initialization).
                    deferred_tables.append((block, current_section))

            elif block.type == BlockType.IMAGE:
                if current_section:
                    current_section.images.append(
                        ImageRef(
                            path=block.image_path,
                            surrounding_text=block.surrounding_text,
                        )
                    )

        # Second pass: extract table-anchored reqs (D-027) when enabled.
        # Disabled corpora (paragraph-only-requirement docs like Verizon
        # OA) drop any id that lives ONLY in a table — those are
        # cross-references, changelog entries, or other non-requirement
        # content per the corpus convention. When enabled, paragraph
        # anchors and struck ids still take precedence (skip_set).
        if self.profile.enable_table_anchored_extraction:
            skip_set = paragraph_req_ids | struck_req_ids
            for tbl_block, parent_section in deferred_tables:
                self._extract_table_anchored_reqs(
                    tbl_block, parent_section, sections, skip_set
                )

        return sections

    # ── Table-anchored requirement detection ────────────────────────

    def _extract_table_anchored_reqs(
        self,
        block: ContentBlock,
        parent_section: Requirement,
        sections: list[Requirement],
        paragraph_req_ids: set[str],
    ) -> None:
        """Detect req-IDs in table cells; append child Requirement nodes to `sections`.

        Heuristic: scan column 1 of each row first; if no IDs there, fall back
        to scanning all cells of the row. At most one anchor per row.
        Skips IDs already anchored by a paragraph elsewhere in the document.
        Within a single table, also dedups so a repeated ID across rows yields
        only one Requirement.
        """
        if not self._req_id_re or not block.rows:
            return

        seen_in_table: set[str] = set()
        for row in block.rows:
            if not row:
                continue

            anchor_id: str | None = None
            anchor_cells: list[str] = list(row)

            # Strategy 1: column 1 only.
            col1_ids = self._find_req_ids(row[0])
            for rid in col1_ids:
                if rid in paragraph_req_ids or rid in seen_in_table:
                    continue
                anchor_id = rid
                break

            # Strategy 2 (fallback): any cell.
            if anchor_id is None:
                for cell in row[1:]:
                    if not cell:
                        continue
                    cell_ids = self._find_req_ids(cell)
                    for rid in cell_ids:
                        if rid in paragraph_req_ids or rid in seen_in_table:
                            continue
                        anchor_id = rid
                        break
                    if anchor_id is not None:
                        break

            if anchor_id is None:
                continue

            seen_in_table.add(anchor_id)
            self._create_table_anchored_req(
                anchor_id, anchor_cells, block, parent_section, sections
            )

    def _create_table_anchored_req(
        self,
        req_id: str,
        row: list[str],
        block: ContentBlock,
        parent_section: Requirement,
        sections: list[Requirement],
    ) -> None:
        """Append a Requirement node anchored by a table row.

        Linkage to parent_section is done here (not via _link_parents, which
        keys on section_number — table-anchored reqs have none). Hierarchy
        path is filled later by _propagate_hierarchy_to_table_reqs once the
        paragraph-anchored sections have their paths built.
        """
        # Serialize row as text using headers when available — preserves the
        # column→value mapping that is the actual content of the requirement.
        headers = block.headers or []
        parts: list[str] = []
        for i, cell in enumerate(row):
            cell_str = (cell or "").strip()
            if not cell_str:
                continue
            if i < len(headers) and headers[i]:
                parts.append(f"{headers[i].strip()}: {cell_str}")
            else:
                parts.append(cell_str)
        text = "; ".join(parts)

        new_req = Requirement(
            req_id=req_id,
            section_number="",   # no own section — anchored by table row
            title="",
            parent_req_id=parent_section.req_id,
            parent_section=parent_section.section_number,
            hierarchy_path=[],   # filled in _propagate_hierarchy_to_table_reqs
            zone_type=parent_section.zone_type,
            text=text,
            tables=[
                TableData(
                    headers=list(block.headers),
                    rows=[list(row)],
                    source="inline",
                )
            ],
        )
        sections.append(new_req)
        if req_id and req_id not in parent_section.children:
            parent_section.children.append(req_id)

    def _propagate_hierarchy_to_table_reqs(
        self, sections: list[Requirement]
    ) -> None:
        """Copy parent's hierarchy_path to table-anchored Requirements.

        _link_parents skips nodes without section_number (table-anchored), so
        their hierarchy_path stays empty after that pass. Fill it now from the
        paragraph-anchored parent. `applicability` is propagated by
        `_apply_applicability` later (it walks document-order so parents
        resolve before children, including table-anchored ones).
        """
        # Lookup paragraph-anchored sections by section_number.
        by_section_num: dict[str, Requirement] = {
            s.section_number: s for s in sections if s.section_number
        }
        for s in sections:
            if s.section_number or not s.parent_section:
                continue
            parent = by_section_num.get(s.parent_section)
            if parent and parent.hierarchy_path:
                s.hierarchy_path = list(parent.hierarchy_path)

    def _apply_applicability(self, sections: list[Requirement]) -> None:
        """FR-32 [D-030]: resolve `Requirement.applicability` for every section.

        Walk in document order. For each section:
          1. Try `requirement_patterns` against the section's own text.
             First-match wins; capture group 1 is split into labels.
          2. Else inherit from `parent_section`'s already-resolved value.
          3. Else fall back to a root default extracted from the
             document-level applicability section, if any.

        No-op when the profile has no patterns and no global section regex.
        Empty list = unknown; downstream stages do not filter on empty.
        """
        # Fast path: nothing to do.
        if not self._applicability_res and self._applicability_global_re is None:
            return

        # Resolve a root default by scanning for the global applicability
        # section once. Its body text is run through requirement_patterns.
        root_default: list[str] = []
        if self._applicability_global_re is not None:
            for s in sections:
                if s.title and self._applicability_global_re.search(s.title):
                    root_default = self._extract_applicability_labels(s.text)
                    if root_default:
                        break

        # Lookup table for parent inheritance — table-anchored reqs key on
        # parent_section, paragraph-anchored on section_number.
        by_section_num: dict[str, Requirement] = {
            s.section_number: s for s in sections if s.section_number
        }

        for s in sections:
            # 1. Explicit value from the section's own text.
            labels = self._extract_applicability_labels(s.text)
            if labels:
                s.applicability = labels
                continue
            # 2. Inherit from parent_section if already resolved (document
            #    order guarantees parents come first for paragraph-anchored;
            #    table-anchored point at parent via parent_section).
            parent_key = s.parent_section
            if parent_key and parent_key in by_section_num:
                parent = by_section_num[parent_key]
                if parent.applicability:
                    s.applicability = list(parent.applicability)
                    continue
            # 3. Root default.
            if root_default:
                s.applicability = list(root_default)

    def _extract_definitions(
        self, sections: list[Requirement]
    ) -> tuple[dict[str, str], str]:
        """FR-35 [D-032]: extract `term -> expansion` pairs and the
        section number of the definitions / acronyms / glossary section.

        Section detection runs `definitions_section_pattern` against each
        section's title. The first match's body text is scanned line by
        line via `definitions_entry_pattern`. The section itself stays in
        the parsed tree (callers may still query it directly); the map
        and section_number are returned for downstream stages. No-op when
        either regex is None.

        Per-document scope — the returned map is stored on
        `RequirementTree.definitions_map` and never aggregated across
        trees. `RAT` may mean different things in different MNO documents.

        Returns (definitions_map, section_number). When no section
        matches, returns ({}, "").
        """
        if self._definitions_section_re is None or self._definitions_entry_re is None:
            return {}, ""

        target: Requirement | None = None
        for s in sections:
            if s.title and self._definitions_section_re.search(s.title):
                target = s
                break
        if target is None or not target.text:
            return {}, (target.section_number if target else "")

        defs: dict[str, str] = {}
        for m in self._definitions_entry_re.finditer(target.text):
            if not m.groups() or len(m.groups()) < 2:
                continue
            term = m.group(1).strip()
            expansion = m.group(2).strip()
            if not term or not expansion:
                continue
            # First definition wins on duplicate term.
            if term in defs:
                continue
            defs[term] = expansion
        return defs, target.section_number

    def _extract_applicability_labels(self, text: str) -> list[str]:
        """Run requirement_patterns over `text`; first match wins. Capture
        group 1 is split into individual labels via `label_split_pattern`.
        Returns [] when no pattern matches or no labels survive trimming.
        """
        if not text or not self._applicability_res:
            return []
        for rx in self._applicability_res:
            m = rx.search(text)
            if not m:
                continue
            captured = (m.group(1) if m.groups() else m.group(0)).strip()
            if not captured:
                continue
            if self._applicability_split_re is not None:
                parts = self._applicability_split_re.split(captured)
            else:
                parts = [captured]
            labels = [p.strip() for p in parts if p and p.strip()]
            # De-duplicate while preserving order.
            seen: set[str] = set()
            unique: list[str] = []
            for label in labels:
                key = label.lower()
                if key in seen:
                    continue
                seen.add(key)
                unique.append(label)
            return unique
        return []

    def _is_req_id_block(self, block: ContentBlock) -> bool:
        """Check if a block is a standalone requirement ID (small font)."""
        if not block.font_info or not self._req_id_re:
            return False
        # Req IDs in VZW docs appear at small font (7pt), distinct from
        # body text (12pt) and headings (14pt)
        body_mid = (
            self.profile.body_text.font_size_min
            + self.profile.body_text.font_size_max
        ) / 2
        if block.font_info.size < body_mid - 2.0:
            return bool(self._req_id_re.search(block.text))
        return False

    def _find_req_ids(self, text: str) -> list[str]:
        """Find all req_id patterns in `text` and canonicalize each.

        Wraps `_req_id_re.findall(text)` to absorb PDF-extraction
        artifacts (whitespace where an underscore should be) — every
        matched id is normalized via `_canonicalize_req_id` so the same
        requirement is never tracked under two different identifiers.
        """
        if not self._req_id_re or not text:
            return []
        return [_canonicalize_req_id(rid) for rid in self._req_id_re.findall(text)]

    def _classify_heading(
        self, block: ContentBlock
    ) -> tuple[str, str]:
        """Check if a block is a heading. Returns (section_number, title) or ("", "").

        Numbering is the necessary signal: a block matching the profile's
        numbering_pattern is a heading candidate. Style/font in
        `profile.heading_detection.levels` is consulted only as a confidence
        hint — never as a gate — because real-world specs apply styling
        inconsistently. Hierarchy depth is derived elsewhere from the
        section_number itself (see _link_parents).

        False-positive guards:
          - text length capped (numbered list items in body text run long)
          - text doesn't end with sentence-terminal punctuation
        """
        # Must have a numbering pattern to be treated as a structural heading
        if not self._num_re:
            return "", ""

        text = block.text.strip()
        m = self._num_re.match(text)
        if not m:
            return "", ""

        # Length guard: real headings are short. Numbered list items in body
        # text typically run >200 chars and end with terminal punctuation —
        # those are not headings.
        if len(text) > _HEADING_MAX_LEN:
            return "", ""
        if text and text[-1] in ".!?":
            # Allow trailing period only on very short titles like "1.1.1.".
            if len(text) > 80:
                return "", ""

        # Extract the section number with a local, known-good regex —
        # independent of the profile's gate-pattern capture shape.
        sec_m = _SECTION_NUM_RE.match(text)
        if not sec_m:
            return "", ""
        section_num = sec_m.group(0).rstrip(".")
        title = text[sec_m.end():].lstrip()

        return section_num, title

    def _extract_priority(self, text: str) -> tuple[str, str]:
        """Extract a priority marker from heading text (FR-31).

        Returns (priority, cleaned_text). The matched substring is stripped
        from the title and the regex's first capture group becomes the
        priority value (uppercased so different casings collapse). When the
        profile pattern is empty or doesn't match, returns ("", text).
        """
        if self._priority_re is None or not text:
            return "", text
        m = self._priority_re.search(text)
        if not m:
            return "", text
        priority = (m.group(1) if m.groups() else m.group(0)).strip().upper()
        # Strip the matched span and collapse any double-spaces it leaves.
        cleaned = (text[:m.start()] + text[m.end():]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return priority, cleaned

    def _classify_zone(self, section_number: str) -> str:
        """Classify a section into a document zone using profile rules."""
        for pattern, zone_type in self._zone_map.items():
            if re.match(pattern, section_number):
                return zone_type
        return ""

    @staticmethod
    def _append_text(section: Requirement, text: str) -> None:
        """Append text to a section, with paragraph separation."""
        text = text.strip()
        if not text:
            return
        if section.text:
            section.text += "\n" + text
        else:
            section.text = text

    # ── Parent-child linking ────────────────────────────────────────

    def _link_parents(self, sections: list[Requirement]) -> None:
        """Build parent-child relationships and hierarchy paths."""
        # Build lookup: section_number → Requirement
        by_section: dict[str, Requirement] = {}
        for sec in sections:
            by_section[sec.section_number] = sec

        for sec in sections:
            if not sec.section_number:
                continue

            # Find parent section number by trimming the last component
            parts = sec.section_number.split(".")
            if len(parts) > 1:
                parent_num = ".".join(parts[:-1])
                parent = by_section.get(parent_num)
                if parent:
                    sec.parent_section = parent_num
                    sec.parent_req_id = parent.req_id
                    if sec.req_id not in parent.children:
                        parent.children.append(sec.req_id or sec.section_number)

            # Build hierarchy path
            sec.hierarchy_path = self._build_hierarchy_path(
                sec.section_number, by_section
            )

    @staticmethod
    def _build_hierarchy_path(
        section_number: str,
        by_section: dict[str, Requirement],
    ) -> list[str]:
        """Build the hierarchy path from root to this section."""
        parts = section_number.split(".")
        path = []
        for i in range(1, len(parts) + 1):
            ancestor_num = ".".join(parts[:i])
            ancestor = by_section.get(ancestor_num)
            if ancestor:
                path.append(ancestor.title or ancestor_num)
        return path

    # ── Cross-reference extraction ──────────────────────────────────

    def _extract_cross_refs(
        self, text: str, own_plan_id: str
    ) -> CrossReferences:
        """Extract cross-references from section text."""
        if not text:
            return CrossReferences()

        refs = CrossReferences()

        # Internal requirement ID references
        if self._req_id_re:
            for rid in self._find_req_ids(text):
                ref_plan = self._extract_plan_id_from_req(rid)
                if ref_plan is not None:
                    if ref_plan != own_plan_id:
                        if ref_plan not in refs.external_plans:
                            refs.external_plans.append(ref_plan)
                    else:
                        if rid not in refs.internal:
                            refs.internal.append(rid)

        # Standards references — with section numbers
        seen_std: set[tuple[str, str]] = set()
        for m in self._std_detail_re.finditer(text):
            spec = f"3GPP TS {m.group(1)}"
            section = m.group(2)
            key = (spec, section)
            if key in seen_std:
                continue
            seen_std.add(key)
            # Try to find release nearby
            release = ""
            context = text[max(0, m.start() - 100):m.end() + 100]
            rm = self._std_release_re.search(context)
            if rm:
                release = f"Release {rm.group(1)}"
            refs.standards.append(
                StandardsRef(spec=spec, section=section, release=release)
            )

        # Standards references — spec only (no section number)
        spec_only_re = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d)")
        for m in spec_only_re.finditer(text):
            spec = f"3GPP TS {m.group(1)}"
            if not any(s.spec == spec for s in refs.standards):
                release = ""
                context = text[max(0, m.start() - 100):m.end() + 100]
                rm = self._std_release_re.search(context)
                if rm:
                    release = f"Release {rm.group(1)}"
                refs.standards.append(
                    StandardsRef(spec=spec, section="", release=release)
                )

        return refs

    def _extract_plan_id_from_req(self, req_id: str) -> str | None:
        """Extract the plan ID component from a requirement ID using profile config."""
        if self._req_id_plan_pos is None:
            return None
        parts = req_id.split(self._req_id_separator)
        pos = self._req_id_plan_pos
        if pos < len(parts):
            return parts[pos]
        return None

    # ── Standards releases ──────────────────────────────────────────

    def _extract_standards_releases(
        self, doc: DocumentIR
    ) -> dict[str, str]:
        """Extract referenced standards releases from the document.

        Scans all text for patterns like:
        - "3GPP TS 24.301 Release 10"
        - "Release 10 version of 3GPP TS 24.301"
        """
        releases: dict[str, str] = {}

        # Pattern 1: "3GPP TS X.X ... Release N"
        pat1 = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d).*?[Rr]elease\s+(\d+)")
        # Pattern 2: "Release N ... 3GPP TS X.X" (VZW style)
        pat2 = re.compile(r"[Rr]elease\s+(\d+)\s+(?:version\s+of\s+)?3GPP\s+TS\s+(\d[\d.]*\d)")

        for b in doc.content_blocks:
            for m in pat1.finditer(b.text):
                spec = f"3GPP TS {m.group(1)}"
                release = f"Release {m.group(2)}"
                if spec not in releases:
                    releases[spec] = release
            for m in pat2.finditer(b.text):
                spec = f"3GPP TS {m.group(2)}"
                release = f"Release {m.group(1)}"
                if spec not in releases:
                    releases[spec] = release

        return releases
