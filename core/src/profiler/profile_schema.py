"""Document structure profile schema (TDD 5.2.3).

The profile is the output of the DocumentProfiler and input to the
GenericStructuralParser. It's a JSON file — machine-readable and
human-editable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class HeadingLevel:
    """Detection rule for a single heading level.

    When `HeadingDetection.method == "numbering"` (the default for spec docs
    with section numbers), this rule is **advisory** — the parser uses
    `numbering_pattern` to identify headings and `section_number.count(".")
    + 1` to assign depth, regardless of font/style. The level rule is kept
    for human curation and as a hint about typical heading styling. When
    `method == "font_size_clustering"` or `"docx_styles"`, the rule is
    consulted for classification (legacy path).
    """
    level: int
    font_size_min: float
    font_size_max: float
    bold: bool | None = None  # None = either bold or not
    all_caps: bool | None = None  # None = either caps or not
    sample_texts: list[str] = field(default_factory=list)
    count: int = 0


@dataclass
class HeadingDetection:
    """Rules for detecting headings and their hierarchy.

    `method` selects the classification strategy:
      - "numbering" (preferred for numbered specs): a block matching
        `numbering_pattern` is a heading; depth from `section_number`.
        `levels` is advisory.
      - "font_size_clustering" (fallback): heading recognized by font/style
        match in `levels`; numbering still required.
      - "docx_styles": DOCX style names drive classification.
    """
    method: str = "numbering"
    levels: list[HeadingLevel] = field(default_factory=list)
    numbering_pattern: str = ""  # regex for section numbers (e.g., "^(\\d+(?:\\.\\d+)*)\\s+\\S")
    max_observed_depth: int = 0

    # FR-31: optional priority-marker regex applied to heading text. When
    # matched, capture group 1 becomes `Requirement.priority` and the matched
    # portion is stripped from the displayed title. Empty string disables.
    # Example for a corpus that uses bracketed markers:
    #   r"\[(MANDATORY|OPTIONAL|CONDITIONAL)\]"
    priority_marker_pattern: str = ""

    # When True, a block matching `numbering_pattern` is promoted to a section
    # heading ONLY if its font also looks like a heading (bold, or larger than
    # `body_text.font_size_max`). Some corpora contain borderless tables whose
    # rows are body-font paragraphs starting with a row number (1, 2, 3…) — the
    # relaxed numbering gate mis-reads each row as its own section. This flag
    # gates those out (real headings are bold/larger; leaked table rows are
    # body-font, non-bold). Default False preserves the legacy "font is advisory
    # only" behavior for corpora whose headings are body-font.
    require_heading_font_for_numbering: bool = False

    # When True, `priority_marker_pattern` is applied ONLY to headings that are
    # themselves requirements (a req_id was resolved for the heading). Some
    # corpora place the priority marker solely on requirement headings, so a
    # structural heading whose title coincidentally contains a marker-shaped
    # token must not be mined (it would gain a spurious priority and lose the
    # token from its title). Default False preserves the general FR-31 contract
    # that any heading may carry a priority marker.
    priority_requires_req_id: bool = False

    # FR-35 [D-032]: regex matching the heading text of a document's
    # definitions / acronyms / glossary section. Default catches common
    # variants. Empty string disables definitions extraction.
    definitions_section_pattern: str = r"(?i)acronym|definition|glossary"

    # Optional regex stripped from EVERY heading title (after priority
    # extraction). For corpora that carry a trailing id-marker INSIDE the
    # heading text — e.g. "<title> ID: <PREFIX>-<TYPE>-<DIGITS>" — so the
    # requirement statement and the hierarchy paths stay clean, for requirement
    # AND structural headings alike (the latter's id doesn't match
    # `requirement_id.pattern`, so it isn't peeled otherwise). Independent of
    # `requirement_id.anchor`. Example: r"\s*ID:\s*\S+\s*$". Empty string disables.
    title_strip_pattern: str = ""

    # Table-header fallback for the glossary section: when a TABLE block
    # has no preceding heading that matches `definitions_section_pattern`
    # but its joined headers (` | `.join(headers)) match this regex, the
    # parser treats the table as the definitions section and extracts
    # entries the same way it would for the label-detected case. Empty
    # disables (default).
    definitions_table_header_pattern: str = ""


@dataclass
class RequirementIdPattern:
    """Detected requirement ID pattern."""
    pattern: str = ""  # regex
    components: dict[str, Any] = field(default_factory=dict)
    sample_ids: list[str] = field(default_factory=list)
    total_found: int = 0

    requirement_type_pattern: str = ""
    """Optional regex identifying which captured ids are ACTUAL requirements vs
    structural sections. When `pattern` is broadened to capture both requirement
    and section ids (e.g. a corpus whose sections also carry ids), a node's
    ``is_requirement`` is True only when its ``req_id`` matches this narrower
    pattern. Empty (default) → any node with a req_id is a requirement
    (back-compat: the historical "req_id present ⇒ requirement" semantics)."""

    anchor: str = "trailing_text"
    """Where in a heading the req_id is anchored:
      - ``"last_run"``: take the last ``TextRun`` of the heading; treat its
        text as the req_id when it matches ``pattern``. Robust to
        partial-strike and inline mentions; requires DOCX-style runs.
      - ``"trailing_text"`` (default): regex-search ``pattern`` against the
        heading's full text, anchored at end. Legacy / OA behavior.
      - ``"leading_text"``: regex-search ``pattern`` anchored at start.
    """

    normalize: str = "none"
    """Normalization applied to the extracted req_id token:
      - ``"none"`` (default): no transformation.
      - ``"upper"``: ``str.upper()`` — needed for corpora whose plan codes
        appear in mixed case in headings (e.g., ``VoWiFi``) but are
        canonical-uppercase elsewhere.
    """

    detection_mode: str = "heading"
    """Where requirements are detected in the document (D-DRAFT-2):
      - ``"heading"`` (default): requirements are heading-anchored — every
        ``Requirement`` is opened by a section heading (or a table-cell
        anchor). The req_id is attached to the heading via ``anchor`` above.
        This is the Verizon OA shape and the historical behavior.
      - ``"leading_id_body"``: requirements are **flat body paragraphs whose
        text begins with the req_id pattern**; section/subsection headings are
        non-requirement context. Each qualifying body block becomes a
        ``Requirement`` parented to the enclosing heading (structurally like a
        table-anchored req: no own ``section_number``). For corpora whose
        sections carry no req_ids and whose requirements lead with their id
        (e.g. ``<PREFIX>-<PLAN>-<DIGITS>``). Pairs with ``anchor`` /
        ``components`` for plan extraction, unchanged.
    """


@dataclass
class MetadataField:
    """Location and pattern for a single metadata field."""
    location: str = ""  # "first_page", "header", etc.
    pattern: str = ""  # regex with capture group
    sample_value: str = ""


@dataclass
class PlanMetadata:
    """Rules for extracting plan-level metadata."""
    plan_name: MetadataField = field(default_factory=MetadataField)
    plan_id: MetadataField = field(default_factory=MetadataField)
    version: MetadataField = field(default_factory=MetadataField)
    release_date: MetadataField = field(default_factory=MetadataField)


@dataclass
class DocumentZone:
    """Classification of a top-level document section."""
    section_pattern: str = ""  # regex matching section number
    zone_type: str = ""  # "introduction", "hardware_specs", "software_specs", "scenarios"
    description: str = ""
    heading_text: str = ""  # the actual heading text observed


@dataclass
class HeaderFooter:
    """Rules for detecting and stripping headers and footers."""
    header_patterns: list[str] = field(default_factory=list)
    footer_patterns: list[str] = field(default_factory=list)
    page_number_pattern: str = ""


@dataclass
class ApplicabilityDetection:
    """Rules for extracting form-factor applicability (FR-32 [D-030]).

    Per D-030, regex-only — no keyword bag-of-words fallback. The corrections
    workflow extends `requirement_patterns` by JSON edit, not code change.
    """

    requirement_patterns: list[str] = field(default_factory=list)
    """Regex patterns matching an applicability statement at requirement /
    section level. First-match wins; capture group 1 contains the form-factor
    text (single label or comma/pipe-separated list, parsed by the parser
    into list[str]). Empty list disables detection."""

    global_section_pattern: str = ""
    """Optional regex matching the heading text of a document-level
    applicability section (e.g. `(?i)^applicability$`). When detected, the
    contents of that section produce the root default by running
    `requirement_patterns` over its body text."""

    label_split_pattern: str = r"[,;|]|\band\b|\bor\b"
    """Regex used to split a captured form-factor list into individual
    labels. Default handles `[, ; |]` plus the words `and`/`or`."""


@dataclass
class CrossReferencePatterns:
    """Regex patterns for detecting cross-references in text."""
    standards_citations: list[str] = field(default_factory=list)
    internal_section_refs: str = ""
    requirement_id_refs: str = ""


@dataclass
class BodyText:
    """Characteristics of normal body text."""
    font_size_min: float = 0.0
    font_size_max: float = 0.0
    font_families: list[str] = field(default_factory=list)


@dataclass
class TocDetection:
    """Style-driven TOC detection (DOCX-authoritative).

    Two-stage detection complementing the legacy text-regex path
    (``DocumentProfile.toc_detection_pattern``):

    1. ``style_pattern`` — regex matched against a paragraph's ``style``
       (e.g. DOCX's auto-generated ``toc 1``, ``toc 2``...). Capture
       group 1 is the depth (1-indexed). When set and a paragraph's
       style matches, the block is a TOC entry regardless of its text
       shape.
    2. ``entry_pattern`` — regex applied to the matched paragraph's
       ``text``. Named groups extracted: ``num`` (full section number,
       e.g. ``"1.2.3"``), ``body`` (title plus optional trailing
       req_id), ``page`` (TOC page-number column). The parser further
       peels the req_id from the tail of ``body`` using
       ``RequirementIdPattern.pattern`` with ``\\s*`` tolerance —
       handles both ``"<title> <req_id>"`` and ``"<title><req_id>"``.

    Empty ``style_pattern`` disables the style-driven path; the parser
    falls back to the text-regex / page-threshold heuristic.
    """

    style_pattern: str = ""
    entry_pattern: str = (
        r"^(?P<num>[\w.]+)\t(?P<body>.+?)\t(?P<page>\d+)\s*$"
    )


@dataclass
class RevhistDetection:
    """Signal-based revision-history TABLE detection.

    Designed for the long tail of revhist variants that regex over the
    joined-header string can't enumerate cleanly: varying column count,
    varying column order, varying header vocabulary (Rev/Revision/Ver,
    Change/Changes/Description of Changes, Author/Owner/Editor, ...),
    and the case where the "Revision History" LABEL lives inside a
    merged cell at the top or bottom of the table rather than in a
    heading paragraph above it.

    Runs AFTER the label and regex-header paths so it never alters
    existing profile behaviour unless ``enabled=True``. When it fires,
    drops the table and arms the same consume-until-next-paragraph
    state both other paths use.

    Score formula:
        position_score   * position_weight    +
        vocab_score      * vocab_weight       +
        cell_score       * cell_weight
            >=  threshold  →  revhist
    Each sub-score is normalized to [0, 1].
    """

    enabled: bool = False

    # ── Position signal ────────────────────────────────────────────
    max_position_fraction: float = 0.30
    """A table at block-index fraction ≤ this contributes a full
    position score (1.0); beyond that, 0.0. Set to 1.0 to disable
    position gating (any table eligible). Default 0.30 covers most
    front-matter revhists; some corpora put the revhist after a long
    multi-page TOC or after a sub-document boundary, pushing the
    revhist past a tight 0.15 cutoff."""

    position_weight: float = 0.25

    # ── Column-vocabulary signal ──────────────────────────────────
    vocab_tokens: list[str] = field(default_factory=lambda: [
        "rev", "revision", "version", "ver",
        "change", "changes", "history", "log",
        "author", "owner", "editor", "by",
        "date", "datetime", "released", "release",
        "description", "comment", "comments", "notes", "summary",
        "approver", "approved",
    ])
    """Case-insensitive tokens looked up in: (a) joined column headers,
    (b) every merged-cell text on the table, (c) every body-row cell.
    Each unique matching token contributes one point, capped at
    ``vocab_score_cap``, then normalized to [0, 1].

    Body-cell scan handles reversed-order tables (column-header row
    buried below data rows; "Revision History" label in a footer-merged
    cell) and any layout where the indicative vocabulary doesn't live
    in row 0."""

    vocab_score_cap: int = 4
    vocab_weight: float = 0.5

    # ── Cell-content fingerprint signal ───────────────────────────
    cell_patterns: list[str] = field(default_factory=lambda: [
        r"^\s*v?\d+(\.\d+)+\s*$",                 # version: 1.0, v2.0, 1.2.3
        r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\s*$",  # date: 11-01-23, 5/10/2017
        r"^\s*\d{4}[/.-]\d{1,2}[/.-]\d{1,2}\s*$",    # ISO date: 2026-01-01
        r"^\s*\d{1,2}\s+\w+\s+\d{4}\s*$",         # date: 11 January 2023
    ])
    """Per-cell regexes. For each pattern, count columns where ≥
    ``cell_min_match_fraction`` of body cells match. Sum across
    patterns, cap at ``cell_score_cap``, normalize to [0, 1]."""

    cell_min_match_fraction: float = 0.5
    cell_score_cap: int = 2
    cell_weight: float = 0.25

    # ── Threshold ──────────────────────────────────────────────────
    threshold: float = 0.55
    """A table whose combined score ≥ this is classified as revhist.
    Default 0.55 chosen so that: position-only (0.25) is not enough;
    position + meaningful vocab (0.25 + ~0.4) clears; even a clean
    vocab-only hit (0.5) on a non-front-matter table clears."""


def _load_revhist_detection(data: dict) -> RevhistDetection:
    """Build a RevhistDetection with per-field defaults preserved when
    keys are absent (caller passes ``data.get('revhist_detection', {})``)."""
    default = RevhistDetection()
    return RevhistDetection(
        enabled=data.get("enabled", default.enabled),
        max_position_fraction=data.get(
            "max_position_fraction", default.max_position_fraction
        ),
        position_weight=data.get("position_weight", default.position_weight),
        vocab_tokens=list(data.get("vocab_tokens", default.vocab_tokens)),
        vocab_score_cap=data.get("vocab_score_cap", default.vocab_score_cap),
        vocab_weight=data.get("vocab_weight", default.vocab_weight),
        cell_patterns=list(data.get("cell_patterns", default.cell_patterns)),
        cell_min_match_fraction=data.get(
            "cell_min_match_fraction", default.cell_min_match_fraction
        ),
        cell_score_cap=data.get("cell_score_cap", default.cell_score_cap),
        cell_weight=data.get("cell_weight", default.cell_weight),
        threshold=data.get("threshold", default.threshold),
    )


@dataclass
class DocumentProfile:
    """Complete document structure profile (TDD 5.2.3).

    Output of the DocumentProfiler. Input to the GenericStructuralParser.
    Designed for human review and manual editing.
    """
    profile_name: str = ""
    profile_version: int = 1
    created_from: list[str] = field(default_factory=list)
    last_updated: str = ""

    heading_detection: HeadingDetection = field(default_factory=HeadingDetection)
    requirement_id: RequirementIdPattern = field(default_factory=RequirementIdPattern)
    plan_metadata: PlanMetadata = field(default_factory=PlanMetadata)
    document_zones: list[DocumentZone] = field(default_factory=list)
    header_footer: HeaderFooter = field(default_factory=HeaderFooter)
    cross_reference_patterns: CrossReferencePatterns = field(
        default_factory=CrossReferencePatterns
    )
    body_text: BodyText = field(default_factory=BodyText)
    applicability_detection: ApplicabilityDetection = field(
        default_factory=ApplicabilityDetection
    )
    definitions_entry_pattern: str = r"^([A-Z][A-Z0-9/-]{1,15})\s*[—–:\-]\s*(.+?)$"
    """Per-line regex extracting `term -> expansion` pairs from the body
    text of the definitions section identified by
    `heading_detection.definitions_section_pattern`. See FR-35 [D-032]."""

    # Enable pdfplumber's text-alignment table detection at EXTRACT time for
    # this corpus — for tables drawn with no ruling lines (the "lines" strategy
    # misses them, so their rows leak out as paragraphs). Per-corpus because the
    # text strategy over-detects on prose; the extract stage reads it per cell so
    # a full multi-MNO run only enables it where the profile asks. Default off.
    detect_text_tables: bool = False

    # Structure source for tables + figures. "" (default) uses the built-in
    # geometric path (pdfplumber tables + pymupdf/borderless detection). A named
    # provider (e.g. "docling") routes tables + figures through a LayoutProvider
    # while the pymupdf text layer + this profile-driven parser still own the
    # requirement text. Per-corpus, opt-in (D-003) — heavy engines stay off for
    # corpora that don't need them.
    layout_provider: str = ""

    # How the PDF extractor treats the top/bottom page margins for header/footer
    # removal. "blanket" (default) drops ALL text in the margin band — correct
    # for corpora whose headers sit in the margin and whose requirements never
    # start that high. "pattern_only" keeps margin text and drops it only when it
    # matches a repeating header/footer, a "Page N of M" line, or a confidential
    # notice — for corpora where a requirement heading can begin at the very top
    # of a page (blanket mode would silently discard it).
    header_footer_margin_mode: str = "blanket"

    # ── Strikeout omission (FR-33 [D-031]) ────────────────────────
    ignore_strikeout: bool = True
    """When True (default), the parser drops content blocks whose
    `font_info.strikethrough` is True — these represent
    requirements/sections deleted in the source document. Flip to False
    via the corrections workflow for corpora that abuse strikethrough
    as emphasis or annotation rather than deletion."""

    enable_table_anchored_extraction: bool = True
    """When True (default), the parser extracts table-anchored Requirements
    per D-027 — req_ids found in table cells that have NO paragraph anchor
    elsewhere become standalone Requirement nodes. Flip to False for
    corpora where requirements are exclusively paragraph-anchored and
    table-cell req_ids are always cross-references, changelog entries,
    or other non-requirement content (Verizon OA is one such corpus).
    With it off, ids that appear only in tables are dropped — the
    cleaner default for paragraph-only-requirement docs at the cost of
    losing genuinely table-defined reqs in MNOs that use that convention."""

    # ── Definitions extraction (FR-35 [D-032]) ────────────────────
    definitions_entry_pattern: str = r"^([A-Z][A-Z0-9/-]{1,15})\s*[—–:\-]\s*(.+?)$"
    """Regex (matched per line of the definitions section's body text)
    extracting term → expansion pairs. Capture group 1 = term (16-char
    cap on the term avoids matching prose lines that happen to start
    uppercase); group 2 = expansion. Empty disables entry extraction
    even if the section is found."""

    # ── TOC omission (FR-34) ──────────────────────────────────────
    toc_detection_pattern: str = r".*\.{3,}\s*\d+\s*$"
    """Regex matching a TOC entry. Default catches the common
    leader-dot-page-number suffix ('Section Title ........ 47'). Empty
    string disables TOC detection. Anchored at end-of-line by default."""

    toc_page_threshold: float = 0.7
    """A page is treated as a TOC page (all blocks dropped wholesale) when
    this fraction or more of its paragraph blocks match
    `toc_detection_pattern`. Range [0.0, 1.0]; 1.0 disables page-level
    drop (only individual matching blocks dropped). Default 0.7 — chosen
    after observing that PDF extractors commonly wrap TOC entries across
    two blocks (the leader-dot suffix lands in a separate block from the
    section title), which lowers the per-page match rate. Real-content
    pages essentially never reach 70% leader-dot patterns."""

    # ── Reference list / bibliography extraction (D-059, D-061) ──
    reference_list_section_pattern: str = (
        r"(?i)^(references|bibliography|normative\s+references)$"
    )
    """Regex matched against section heading text. When a section's
    title matches, its body text + tables are scanned via
    `reference_list_entry_pattern` to populate
    `RequirementTree.reference_list_map`. The map is later used by the
    resolver to resolve indirect spec citations (`[5]`-style references
    that point at a numbered entry in this section). Empty disables.
    Companion to `definitions_section_pattern` — same pattern, applied
    to the bibliography instead of the glossary."""

    reference_list_entry_pattern: str = (
        r"^\s*[\[\(]?(\d+)[\]\)\.]?\s+(.+?)\s*$"
    )
    """Per-line regex extracting `(number, spec)` pairs from the body
    text of the reference list section. Capture group 1 = entry number;
    group 2 = entry content (typically `<spec name>, "<title>"` or
    `<spec name>, §<section>`). Default tolerates bracketed (`[5]`),
    parenthesized (`(5)`), and plain (`5.`) numbering. The parser
    extracts the spec name from group 2 with light heuristics
    (everything up to the first comma / quote / em-dash). Empty
    disables entry extraction even if the section is found."""

    content_end_marker: str = ""
    """Regex matched against each *line* of a requirement's body text. When a
    line matches, the requirement's text is truncated **before** that line and
    the requirement's trailing `tables` + `images` are dropped (D-DRAFT-13,
    strand `multi-mno-nora`). For a non-normative **trailing appendix** that the
    parser glued onto the last real requirement — e.g. a requirement→test-case
    traceability matrix rendered as text + test-case tables, which begins on its
    own line at the end of the document rather than under its own heading (so
    `exclude_section_pattern` can't catch it). Assumes the marker begins a
    trailing appendix; the matched requirement keeps its real body (everything
    before the marker). May contain placeholders (e.g. `<TRACEABILITY>`).
    Empty (default) = no-op."""

    exclude_section_pattern: str = ""
    """Regex matched against a section's *title*. Any section whose title
    matches is dropped from the parsed tree along with its descendants — it
    never becomes a Requirement or a RAG chunk (D-DRAFT-13, strand
    `multi-mno-nora`). For non-normative sections that carry a heading (and
    sometimes a trailing req_id) but aren't real requirements: bibliographies
    (``REFERENCES``), requirement→test-case traceability matrices, etc. Runs
    *after* `reference_list_section_pattern` extraction, so a REFERENCES
    section still populates `reference_list_map` before being dropped from
    chunks. Empty (default) = no-op, so other corpora are unaffected.
    May contain placeholders (e.g. ``<TRACEABILITY>``) resolved from the
    mapping file like any other regex field."""

    # ── Revision/version history omission (FR-34) ────────────────
    revision_history_label_pattern: str = (
        r"(?i)^\s*(?:\S+\s+)*(revision|change|version|document)\s+(history|log)\s*$"
    )
    """Regex matched against the *text* of any paragraph or heading
    block. When a block matches AND the immediately-following block is
    a table, the parser drops both — the label and the
    revision/change-log table itself. Renamed from
    ``revision_history_heading_pattern`` (legacy) to reflect that the
    label can be either heading-styled or a plain paragraph. Empty
    string disables. The default covers common labels across MNOs
    ('Revision History', 'Change History', 'Version History', 'Document
    History', 'Change Log', 'Revision Log'), with an optional
    ``(?:\\S+\\s+)*`` prefix that absorbs section-number-style
    prefixes ('1 Revision History', '1.1 Revision History',
    'A. Revision History', '. Revision History'). The ``$`` anchor
    still prevents body-prose matches. The profiler narrows the regex
    to the specific phrasing observed in a corpus
    (whitespace-tolerant), and the corrections workflow can override
    per-MNO."""

    revhist_table_header_pattern: str = ""
    """Table-header fallback for the revision-history drop: when a TABLE
    block has no preceding label that matched
    ``revision_history_label_pattern`` but its joined headers (joined
    with ` | `) match this regex, the parser drops the table and arms
    the same consume-until-next-paragraph state the label path uses.
    Empty disables (default). Use this for corpora that begin with a
    bare revision-history table and no introducing heading — common in
    cover-page-style docs. The corrections-driven profile_miner emits
    proposals against this field when the user annotates a table block
    as ``revhist``.

    Largely superseded by ``revhist_detection`` (signal-based scoring,
    same paragraph-label gate) which handles the long tail of
    column-order / vocabulary / merged-cell variants without regex
    enumeration. Kept for back-compat and for corpora where a single
    crisp regex already exists."""

    revhist_detection: RevhistDetection = field(default_factory=lambda: RevhistDetection())
    """Signal-based revhist table detection. When enabled, runs as a
    third detection path AFTER ``revision_history_label_pattern`` and
    ``revhist_table_header_pattern`` so existing profiles keep their
    behaviour. Combines position + column-vocabulary + cell-content
    fingerprints into a single score; threshold decides. See
    ``RevhistDetection`` for the per-signal contract."""

    # ── Style-driven TOC detection (generic-rules pivot) ─────────
    toc_detection: TocDetection = field(default_factory=TocDetection)
    """Style-driven TOC detection. Takes priority over
    ``toc_detection_pattern`` when ``toc_detection.style_pattern`` is
    non-empty. See ``TocDetection`` docstring for the two-stage
    matching protocol."""

    # ── Glossary / definitions table extraction ──────────────────
    definitions_table_term_column: str = (
        r"(?i)^\s*(acronym|abbrev|abbreviation|term)([/\s]+\w+)*\s*$"
    )
    """Regex matched against a table column header to identify the
    *term* column of a glossary/definitions table. When the
    definitions section is detected via
    ``heading_detection.definitions_section_pattern`` and the next
    table's columns include one matching this pattern AND one matching
    ``definitions_table_definition_column``, the parser extracts
    ``(term, definition)`` pairs by name. When neither column header
    matches, falls back to positional (col 0 = term, col 1 =
    definition). Empty disables the named-column path (positional
    only)."""

    definitions_table_definition_column: str = (
        r"(?i)^\s*(definition|meaning|description|expansion)([/\s]+\w+)*\s*$"
    )
    """Companion to ``definitions_table_term_column``. Regex matched
    against a table column header to identify the *definition* column.
    Both must match for named-column extraction to apply."""

    embed_glossary: bool = True
    """When True (default — preserves OA behavior), the glossary section
    and per-acronym chunks are emitted into the vector store and
    knowledge graph. When False (generic-rules pivot), the glossary
    section + descendants are dropped from ``RequirementTree.requirements``
    and ``ChunkBuilder._build_glossary_chunks`` is skipped — the
    ``definitions_map`` is preserved so acronym-expansion in body
    chunks (``ChunkBuilder._expand_definitions``) still applies before
    embedding. Use False for corpora where the glossary is purely an
    expansion lookup, not retrieval-relevant content."""

    content_start_section: str = ""
    """D-DRAFT-4: top-level chapter/section number where requirement content
    begins (e.g. ``"3"``). When set, the parser drops every block before the
    first **real heading** (heading-level font — so a same-numbered TOC entry,
    which is body-size, does not trigger it) whose top-level section number
    equals this value. One positive anchor skips the front page, TOC, and intro
    chapters in one shot, for corpora whose front matter isn't reliably
    detectable negatively. Empty (default) = disabled (no cutoff)."""

    build_context: str = "none"
    build_context_max_chars: int = 0
    """D-DRAFT-5: how much enclosing-section context a consumer should attach to
    each requirement (the SIRA adapter and NORA chunk builder assemble it from
    the section nodes at emit time — it is *not* materialized in the parsed tree,
    to avoid per-requirement duplication). One of:
      - ``"none"`` (default) — no context.
      - ``"path"`` — the ancestor section/subsection **numbers + titles** as a
        breadcrumb (``"5 Bands > 5.1 Frequency > 5.1.2 LTE"``); no body.
      - ``"path_and_content"`` — the breadcrumb plus each ancestor section's body
        text. Use for corpora (e.g. leading-id) where sections are non-requirement
        context the synthesizer needs in full.
    A generic, per-corpus knob — independent of `detection_mode`."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> DocumentProfile:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> DocumentProfile:
        hd = data.get("heading_detection", {})
        heading_levels = [HeadingLevel(**lv) for lv in hd.get("levels", [])]

        pm = data.get("plan_metadata", {})
        zones = [DocumentZone(**z) for z in data.get("document_zones", [])]

        hf = data.get("header_footer", {})
        cr = data.get("cross_reference_patterns", {})
        bt = data.get("body_text", {})

        return cls(
            profile_name=data.get("profile_name", ""),
            profile_version=data.get("profile_version", 1),
            created_from=data.get("created_from", []),
            last_updated=data.get("last_updated", ""),
            heading_detection=HeadingDetection(
                method=hd.get("method", "font_size_clustering"),
                levels=heading_levels,
                numbering_pattern=hd.get("numbering_pattern", ""),
                max_observed_depth=hd.get("max_observed_depth", 0),
                priority_marker_pattern=hd.get("priority_marker_pattern", ""),
                require_heading_font_for_numbering=hd.get(
                    "require_heading_font_for_numbering", False
                ),
                priority_requires_req_id=hd.get("priority_requires_req_id", False),
                title_strip_pattern=hd.get("title_strip_pattern", ""),
                definitions_section_pattern=hd.get(
                    "definitions_section_pattern", r"(?i)acronym|definition|glossary"
                ),
                definitions_table_header_pattern=hd.get(
                    "definitions_table_header_pattern", ""
                ),
            ),
            requirement_id=RequirementIdPattern(
                **data.get("requirement_id", {})
            ),
            plan_metadata=PlanMetadata(
                plan_name=MetadataField(**pm.get("plan_name", {})) if pm.get("plan_name") else MetadataField(),
                plan_id=MetadataField(**pm.get("plan_id", {})) if pm.get("plan_id") else MetadataField(),
                version=MetadataField(**pm.get("version", {})) if pm.get("version") else MetadataField(),
                release_date=MetadataField(**pm.get("release_date", {})) if pm.get("release_date") else MetadataField(),
            ),
            document_zones=zones,
            header_footer=HeaderFooter(
                header_patterns=hf.get("header_patterns", []),
                footer_patterns=hf.get("footer_patterns", []),
                page_number_pattern=hf.get("page_number_pattern", ""),
            ),
            cross_reference_patterns=CrossReferencePatterns(
                standards_citations=cr.get("standards_citations", []),
                internal_section_refs=cr.get("internal_section_refs", ""),
                requirement_id_refs=cr.get("requirement_id_refs", ""),
            ),
            body_text=BodyText(
                font_size_min=bt.get("font_size_min", 0),
                font_size_max=bt.get("font_size_max", 0),
                font_families=bt.get("font_families", []),
            ),
            applicability_detection=ApplicabilityDetection(
                **data.get("applicability_detection", {})
            ),
            detect_text_tables=data.get("detect_text_tables", False),
            layout_provider=data.get("layout_provider", ""),
            header_footer_margin_mode=data.get(
                "header_footer_margin_mode", "blanket"
            ),
            ignore_strikeout=data.get("ignore_strikeout", True),
            enable_table_anchored_extraction=data.get(
                "enable_table_anchored_extraction", True
            ),
            definitions_entry_pattern=data.get(
                "definitions_entry_pattern",
                r"^([A-Z][A-Z0-9/-]{1,15})\s*[—–:\-]\s*(.+?)$",
            ),
            toc_detection_pattern=data.get("toc_detection_pattern", r".*\.{3,}\s*\d+\s*$"),
            toc_page_threshold=data.get("toc_page_threshold", 0.8),
            revision_history_label_pattern=data.get(
                "revision_history_label_pattern",
                data.get(
                    "revision_history_heading_pattern",
                    r"(?i)^\s*(revision|change|version|document)\s+(history|log)\s*$",
                ),
            ),
            revhist_table_header_pattern=data.get(
                "revhist_table_header_pattern", ""
            ),
            revhist_detection=_load_revhist_detection(
                data.get("revhist_detection", {}) or {}
            ),
            toc_detection=TocDetection(
                **data.get("toc_detection", {})
            ),
            definitions_table_term_column=data.get(
                "definitions_table_term_column",
                r"(?i)^\s*(acronym|abbrev|abbreviation|term)([/\s]+\w+)*\s*$",
            ),
            definitions_table_definition_column=data.get(
                "definitions_table_definition_column",
                r"(?i)^\s*(definition|meaning|description|expansion)([/\s]+\w+)*\s*$",
            ),
            embed_glossary=data.get("embed_glossary", True),
            reference_list_section_pattern=data.get(
                "reference_list_section_pattern",
                r"(?i)^(references|bibliography|normative\s+references)$",
            ),
            reference_list_entry_pattern=data.get(
                "reference_list_entry_pattern",
                r"^\s*[\[\(]?(\d+)[\]\)\.]?\s+(.+?)\s*$",
            ),
            exclude_section_pattern=data.get("exclude_section_pattern", ""),
            content_end_marker=data.get("content_end_marker", ""),
            content_start_section=data.get("content_start_section", ""),
            build_context=data.get("build_context", "none"),
            build_context_max_chars=int(data.get("build_context_max_chars", 0) or 0),
        )
