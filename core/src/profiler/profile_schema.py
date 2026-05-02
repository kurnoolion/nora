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

    # FR-35 [D-032]: regex matching the heading text of a document's
    # definitions / acronyms / glossary section. Default catches common
    # variants. Empty string disables definitions extraction.
    definitions_section_pattern: str = r"(?i)acronym|definition|glossary"


@dataclass
class RequirementIdPattern:
    """Detected requirement ID pattern."""
    pattern: str = ""  # regex
    components: dict[str, Any] = field(default_factory=dict)
    sample_ids: list[str] = field(default_factory=list)
    total_found: int = 0


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
                definitions_section_pattern=hd.get(
                    "definitions_section_pattern", r"(?i)acronym|definition|glossary"
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
        )
