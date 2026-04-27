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
    """Detection rule for a single heading level."""
    level: int
    font_size_min: float
    font_size_max: float
    bold: bool | None = None  # None = either bold or not
    all_caps: bool | None = None  # None = either caps or not
    sample_texts: list[str] = field(default_factory=list)
    count: int = 0


@dataclass
class HeadingDetection:
    """Rules for detecting headings and their hierarchy."""
    method: str = "font_size_clustering"  # or "docx_styles"
    levels: list[HeadingLevel] = field(default_factory=list)
    numbering_pattern: str = ""  # regex for section numbers (e.g., "^(\\d+\\.)+\\d*\\s")
    max_observed_depth: int = 0


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
        )
