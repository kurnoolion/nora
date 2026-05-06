"""Style-driven parser for DOCX-origin DocumentIR — no profile required.

Heading hierarchy is derived from Word heading levels already stamped on
ContentBlock by DOCXExtractor (block.type == HEADING, block.level == N).
VZ_REQ_ IDs are split from heading text.  Produces the same RequirementTree
as GenericStructuralParser so downstream stages are unaffected.

Use via parse_cli --no-profile, or instantiate StyleDrivenParser() directly.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from core.src.models.document import BlockType, DocumentIR
from core.src.parser.structural_parser import (
    CrossReferences,
    ImageRef,
    ParseStats,
    Requirement,
    RequirementTree,
    StandardsRef,
    TableData,
)

logger = logging.getLogger(__name__)

# ── Patterns ─────────────────────────────────────────────────────────

_VZ_REQ_RE = re.compile(r"VZ_REQ_[A-Z0-9_]+\d+")

_META_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("plan_name",    re.compile(r"^Plan\s+Name:\s*(.+)$",      re.IGNORECASE)),
    ("plan_id",      re.compile(r"^Plan\s+Id:\s*(.+)$",        re.IGNORECASE)),
    ("version",      re.compile(r"^Version\s+Number:\s*(.+)$", re.IGNORECASE)),
    ("release_date", re.compile(r"^Release\s+Date:\s*(.+)$",   re.IGNORECASE)),
]

# Standards — 3GPP with explicit section number
_STD_DETAIL_RE = re.compile(
    r"3GPP\s+TS\s+(\d[\d.]*\d)\s+(?:[Ss]ection\s+)?(\d[\d.]*\d)"
)
# Standards — 3GPP spec only (no section)
_STD_SPEC_RE = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d)")
# Release number in nearby context
_STD_RELEASE_RE = re.compile(r"[Rr]elease\s+(\d+)")
# OMA DM
_OMA_RE = re.compile(r"(?:OMADM|OMA\s+DM)\s+[\d.]+")
_OMA_SECTION_RE = re.compile(r"[Ss]ection\s+([\d.]+)")
_CONTEXT_WINDOW = 200


# ── Parser ────────────────────────────────────────────────────────────


class StyleDrivenParser:
    """Parse a DOCX-origin DocumentIR using heading styles; no profile needed.

    Heading levels come from ContentBlock.level (set by DOCXExtractor from
    Word heading styles).  VZ_REQ_ IDs are split from heading text at the
    first occurrence of the VZ_REQ_ marker.  Metadata (plan_name, plan_id,
    version, release_date) is scraped from pre-heading paragraphs via fixed
    label patterns identical to those used in the standalone JSON_parser_tree
    pipeline.
    """

    def parse(self, doc: DocumentIR) -> RequirementTree:
        logger.info("Style-driven parse: %s", doc.source_file)

        meta: dict[str, str] = {
            "plan_name": "", "plan_id": "", "version": "", "release_date": ""
        }

        # Section counter: counters[level] = current count at that level (1-based)
        counters: dict[int, int] = {}
        # Heading stack: (level, req_id, section_number, title)
        heading_stack: list[tuple[int, str, str, str]] = []
        # Nodes by req_id for child appending
        nodes: dict[str, Requirement] = {}
        requirements: list[Requirement] = []
        current: Optional[Requirement] = None
        in_heading_phase = False

        for block in doc.content_blocks:

            if block.type == BlockType.HEADING:
                in_heading_phase = True
                text = (block.text or "").strip()
                level = block.level
                if not text or level is None:
                    continue

                title, req_id = _split_heading(text)
                if req_id is None:
                    logger.debug("Heading has no VZ_REQ_ — skipped: %r", text)
                    continue

                # Update section counters
                counters[level] = counters.get(level, 0) + 1
                for deeper in list(counters):
                    if deeper > level:
                        counters[deeper] = 0
                section_number = ".".join(
                    str(counters.get(lv, 0)) for lv in range(1, level + 1)
                )

                # Update heading stack
                heading_stack = [e for e in heading_stack if e[0] < level]
                heading_stack.append((level, req_id, section_number, title))

                parent_entry = next(
                    (e for e in reversed(heading_stack[:-1]) if e[0] == level - 1),
                    None,
                )
                parent_req_id  = parent_entry[1] if parent_entry else ""
                parent_section = parent_entry[2] if parent_entry else ""
                hierarchy_path = [e[3] for e in heading_stack]
                zone_type = next(
                    (e[3] for e in heading_stack if e[0] == 1), title
                )

                node = Requirement(
                    req_id=req_id,
                    section_number=section_number,
                    title=title,
                    parent_req_id=parent_req_id,
                    parent_section=parent_section,
                    hierarchy_path=hierarchy_path,
                    zone_type=zone_type,
                )
                if parent_req_id and parent_req_id in nodes:
                    nodes[parent_req_id].children.append(req_id)
                nodes[req_id] = node
                requirements.append(node)
                current = node

            elif block.type == BlockType.PARAGRAPH:
                text = (block.text or "").strip()
                if not text:
                    continue

                if not in_heading_phase:
                    _try_extract_metadata(text, meta)
                    continue

                if current is None:
                    continue

                current.text = (
                    current.text + "\n" + text if current.text else text
                )

            elif block.type == BlockType.TABLE:
                if current is not None:
                    current.tables.append(TableData(
                        headers=block.headers or [],
                        rows=block.rows or [],
                        source="inline",
                    ))

            elif block.type == BlockType.IMAGE:
                if current is not None:
                    current.images.append(ImageRef(
                        path=block.image_path or "",
                        surrounding_text=block.surrounding_text or "",
                    ))

        # Extract cross-references for each requirement
        for req in requirements:
            req.cross_references = _extract_cross_refs(req.text)

        std_releases = _extract_standards_releases(doc)

        tree = RequirementTree(
            mno=doc.mno,
            release=doc.release,
            plan_id=meta["plan_id"],
            plan_name=meta["plan_name"],
            version=meta["version"],
            release_date=meta["release_date"],
            referenced_standards_releases=std_releases,
            requirements=requirements,
            parse_stats=ParseStats(),
        )
        logger.info(
            "Style-driven parse done: %s requirements, plan_id=%s",
            len(requirements), tree.plan_id,
        )
        return tree


# ── Helpers (module-level, no parser state needed) ────────────────────


def _split_heading(text: str) -> tuple[str, Optional[str]]:
    """Split 'Title VZ_REQ_PLAN_123' → ('Title', 'VZ_REQ_PLAN_123').

    Returns (text, None) if no VZ_REQ_ token is found.
    """
    if "VZ_REQ_" not in text:
        return text, None
    parts = text.split("VZ_REQ_", 1)
    title  = parts[0].strip()
    req_id = ("VZ_REQ_" + parts[1]).strip()
    return title, req_id


def _try_extract_metadata(text: str, meta: dict[str, str]) -> None:
    for field_name, pattern in _META_PATTERNS:
        m = pattern.match(text)
        if m:
            meta[field_name] = m.group(1).strip()
            return


def _extract_cross_refs(text: str) -> CrossReferences:
    if not text:
        return CrossReferences()

    refs = CrossReferences()

    # Internal VZ_REQ_ references — caller's own req_id filtering is upstream
    for m in _VZ_REQ_RE.finditer(text):
        rid = m.group()
        if rid not in refs.internal:
            refs.internal.append(rid)

    # 3GPP with explicit section number (more specific — run first)
    seen_detailed: set[tuple[str, str]] = set()
    for m in _STD_DETAIL_RE.finditer(text):
        spec    = f"3GPP TS {m.group(1)}"
        section = m.group(2)
        key = (spec, section)
        if key in seen_detailed:
            continue
        seen_detailed.add(key)
        ctx = text[max(0, m.start() - 100): m.end() + 100]
        rm  = _STD_RELEASE_RE.search(ctx)
        refs.standards.append(StandardsRef(
            spec=spec,
            section=section,
            release=f"Release {rm.group(1)}" if rm else "",
        ))

    # 3GPP spec-only (no section number found by detail pattern)
    for m in _STD_SPEC_RE.finditer(text):
        spec = f"3GPP TS {m.group(1)}"
        if any(s.spec == spec for s in refs.standards):
            continue
        ctx = text[max(0, m.start() - 100): m.end() + 100]
        rm  = _STD_RELEASE_RE.search(ctx)
        refs.standards.append(StandardsRef(
            spec=spec,
            section="",
            release=f"Release {rm.group(1)}" if rm else "",
        ))

    # OMA DM references
    for m in _OMA_RE.finditer(text):
        raw  = m.group()
        spec = re.sub(r"OMA\s+DM", "OMADM", raw)
        spec = re.sub(r"\s+", " ", spec).strip()
        ws   = max(0, m.start() - _CONTEXT_WINDOW)
        we   = min(len(text), m.end() + _CONTEXT_WINDOW)
        win  = text[ws:we]
        sec_m = _OMA_SECTION_RE.search(win)
        rel_m = _STD_RELEASE_RE.search(win)
        refs.standards.append(StandardsRef(
            spec=spec,
            section=sec_m.group(1) if sec_m else "",
            release=f"Release {rel_m.group(1)}" if rel_m else "",
        ))

    return refs


def _extract_standards_releases(doc: DocumentIR) -> dict[str, str]:
    """Scan all blocks for 3GPP spec+release pairs; build top-level dict."""
    releases: dict[str, str] = {}
    pat1 = re.compile(r"3GPP\s+TS\s+(\d[\d.]*\d).*?[Rr]elease\s+(\d+)")
    pat2 = re.compile(
        r"[Rr]elease\s+(\d+)\s+(?:version\s+of\s+)?3GPP\s+TS\s+(\d[\d.]*\d)"
    )
    for b in doc.content_blocks:
        if not b.text:
            continue
        for m in pat1.finditer(b.text):
            spec = f"3GPP TS {m.group(1)}"
            if spec not in releases:
                releases[spec] = f"Release {m.group(2)}"
        for m in pat2.finditer(b.text):
            spec = f"3GPP TS {m.group(2)}"
            if spec not in releases:
                releases[spec] = f"Release {m.group(1)}"
    return releases
