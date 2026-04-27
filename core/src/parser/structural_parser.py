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

from src.models.document import BlockType, ContentBlock, DocumentIR
from src.profiler.profile_schema import DocumentProfile, HeadingLevel

logger = logging.getLogger(__name__)


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
    text: str = ""
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageRef] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # child req_ids
    cross_references: CrossReferences = field(default_factory=CrossReferences)


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
        return cls(
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            plan_id=data.get("plan_id", ""),
            plan_name=data.get("plan_name", ""),
            version=data.get("version", ""),
            release_date=data.get("release_date", ""),
            referenced_standards_releases=data.get("referenced_standards_releases", {}),
            requirements=reqs,
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

        # 1. Extract plan metadata
        plan_meta = self._extract_plan_metadata(doc)

        # 2. Classify blocks and build section hierarchy
        sections = self._build_sections(doc)

        # 3. Extract referenced standards releases
        std_releases = self._extract_standards_releases(doc)

        # 4. Extract cross-references for each section
        plan_id = plan_meta.get("plan_id", "")
        for sec in sections:
            sec.cross_references = self._extract_cross_refs(sec.text, plan_id)

        # 5. Build parent-child relationships
        self._link_parents(sections)

        tree = RequirementTree(
            mno=doc.mno,
            release=doc.release,
            plan_id=plan_meta.get("plan_id", ""),
            plan_name=plan_meta.get("plan_name", ""),
            version=plan_meta.get("version", ""),
            release_date=plan_meta.get("release_date", ""),
            referenced_standards_releases=std_releases,
            requirements=sections,
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

    def _build_sections(self, doc: DocumentIR) -> list[Requirement]:
        """Build the flat list of sections with hierarchy info from content blocks."""
        sections: list[Requirement] = []
        current_section: Requirement | None = None

        # Pending req ID — small font blocks that appear before/after a heading
        pending_req_id: str = ""

        for block in doc.content_blocks:
            if block.type == BlockType.PARAGRAPH:
                if not block.font_info:
                    if current_section:
                        self._append_text(current_section, block.text)
                    continue

                # Check if this is a requirement ID block (small font)
                if self._is_req_id_block(block):
                    req_ids = self._req_id_re.findall(block.text) if self._req_id_re else []
                    if req_ids:
                        # If we have a current section without a req_id, assign it
                        if current_section and not current_section.req_id:
                            current_section.req_id = req_ids[0]
                        else:
                            pending_req_id = req_ids[0]
                    continue

                # Check if this is a heading block
                section_num, heading_text = self._classify_heading(block)
                if section_num:
                    # New section
                    current_section = Requirement(
                        section_number=section_num,
                        title=heading_text,
                        req_id=pending_req_id,
                        zone_type=self._classify_zone(section_num),
                    )
                    pending_req_id = ""
                    sections.append(current_section)
                    continue

                # Body text — append to current section
                if current_section:
                    self._append_text(current_section, block.text)
                    # Also check for inline req IDs in body text
                    if self._req_id_re and not current_section.req_id:
                        ids = self._req_id_re.findall(block.text)
                        if ids:
                            current_section.req_id = ids[0]

            elif block.type == BlockType.TABLE:
                if current_section:
                    current_section.tables.append(
                        TableData(
                            headers=block.headers,
                            rows=block.rows,
                            source="inline",
                        )
                    )

            elif block.type == BlockType.IMAGE:
                if current_section:
                    current_section.images.append(
                        ImageRef(
                            path=block.image_path,
                            surrounding_text=block.surrounding_text,
                        )
                    )

        return sections

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

    def _classify_heading(
        self, block: ContentBlock
    ) -> tuple[str, str]:
        """Check if a block is a heading. Returns (section_number, title) or ("", "")."""
        if not block.font_info:
            return "", ""

        # Check against profile heading levels
        is_heading_font = False
        for lv in self.profile.heading_detection.levels:
            if lv.font_size_min <= block.font_info.size <= lv.font_size_max:
                if lv.bold is not None and block.font_info.bold != lv.bold:
                    continue
                is_heading_font = True
                break

        if not is_heading_font:
            return "", ""

        # Must have a section number to be treated as a structural heading
        if not self._num_re:
            return "", ""

        text = block.text.strip()
        m = self._num_re.match(text)
        if not m:
            return "", ""

        section_num = m.group(0).strip().rstrip(".")
        # Clean: ensure consistent format (remove trailing dots)
        section_num = re.sub(r"\.$", "", section_num)
        title = text[m.end():].strip()

        return section_num, title

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
            for rid in self._req_id_re.findall(text):
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
