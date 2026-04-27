"""Standards reference collector (TDD 5.6, Step 1).

Scans cross-reference manifests AND requirement tree text to collect
all standards references. Aggregates by (spec, release) and extracts
section-level references from requirement body text.

Generic: works with any MNO's documents — no hardcoded spec lists or
MNO-specific logic. References are discovered purely from what the
requirement documents cite.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from src.standards.schema import AggregatedSpecRef, StandardsReferenceIndex

logger = logging.getLogger(__name__)

# Patterns for extracting section-level references from requirement text.
# These cover the major citation styles used across MNO documents.
_SECTION_PATTERNS = [
    # "TS 24.301, section 5.5.3.2.5" or "TS 24.301 section 5.5.3"
    re.compile(
        r"(?:3GPP\s+)?TS\s+(\d[\d.]*\d)\s*,?\s*"
        r"(?:[Ss]ection|[Cc]lause|§)\s+([\d]+(?:\.[\d]+)*)"
    ),
    # "section 5.5.1.2.5 of 3GPP TS 24.301"
    re.compile(
        r"(?:[Ss]ection|[Cc]lause|§)\s+([\d]+(?:\.[\d]+)*)\s+"
        r"of\s+(?:3GPP\s+)?TS\s+(\d[\d.]*\d)"
    ),
    # "TS 24.301 [5] section 5.5.3" (with reference number in brackets)
    re.compile(
        r"(?:3GPP\s+)?TS\s+(\d[\d.]*\d)\s*"
        r"(?:\(?\s*(?:reference\s*)?\[[\d]+\]\s*\)?)\s*,?\s*"
        r"(?:[Ss]ection|[Cc]lause)\s+([\d]+(?:\.[\d]+)*)"
    ),
]

# Pattern for release numbers: "Release 11", "Rel-11", "R11", "rel11"
_RELEASE_PATTERN = re.compile(
    r"[Rr](?:elease|el)[- ]?(\d+)", re.IGNORECASE
)


def _clean_spec_number(raw: str) -> str:
    """Normalize a spec number: strip prefix, trailing dots."""
    # Remove "3GPP TS " prefix if present
    s = raw.strip()
    if s.startswith("3GPP TS "):
        s = s[8:]
    elif s.startswith("TS "):
        s = s[3:]
    # Strip trailing dots
    s = s.rstrip(".")
    return s


def _parse_release_num(release_str: str) -> int:
    """Extract numeric release from strings like 'Release 11', 'Rel-15'."""
    m = _RELEASE_PATTERN.search(release_str)
    if m:
        return int(m.group(1))
    # Try bare number
    try:
        return int(release_str.strip())
    except (ValueError, AttributeError):
        return 0


class StandardsReferenceCollector:
    """Collect and aggregate standards references from requirement documents.

    Sources:
    1. Cross-reference manifests (data/resolved/*_xrefs.json) — spec + release
    2. Requirement tree text (data/parsed/*_tree.json) — section-level detail
    """

    def collect(
        self,
        manifest_dir: Path | None = None,
        trees_dir: Path | None = None,
        manifest_files: list[Path] | None = None,
        tree_files: list[Path] | None = None,
    ) -> StandardsReferenceIndex:
        """Collect all standards references and produce an aggregated index.

        Provide either directories or explicit file lists (not both).
        """
        # Gather file lists
        m_files = self._resolve_files(manifest_dir, manifest_files, "*_xrefs.json")
        t_files = self._resolve_files(trees_dir, tree_files, "*_tree.json")

        # (spec_clean, release_num) → accumulated data
        agg: dict[tuple[str, int], _AccEntry] = defaultdict(_AccEntry)

        # Phase 1: Collect from resolved cross-reference manifests
        plans_seen = set()
        total_refs = 0
        for f in m_files:
            n = self._collect_from_manifest(f, agg)
            total_refs += n
            plans_seen.add(f.stem.replace("_xrefs", ""))

        # Phase 2: Enrich with section-level refs from tree text
        for f in t_files:
            n = self._collect_sections_from_tree(f, agg)
            total_refs += n
            plans_seen.add(f.stem.replace("_tree", ""))

        # Build output
        specs = []
        for (spec, rel_num), entry in sorted(agg.items()):
            specs.append(AggregatedSpecRef(
                spec=spec,
                release=f"Release {rel_num}" if rel_num else "",
                release_num=rel_num,
                sections=sorted(set(entry.sections)),
                source_plans=sorted(entry.source_plans),
                ref_count=entry.ref_count,
            ))

        index = StandardsReferenceIndex(
            specs=specs,
            total_refs=total_refs,
            total_unique_specs=len(set(s.spec for s in specs)),
            source_documents=sorted(plans_seen),
        )

        logger.info(
            f"Collected {total_refs} references to "
            f"{index.total_unique_specs} unique specs "
            f"({len(specs)} spec-release pairs) "
            f"from {len(plans_seen)} documents"
        )
        return index

    def _collect_from_manifest(
        self, path: Path, agg: dict[tuple[str, int], _AccEntry]
    ) -> int:
        """Extract spec references from a resolved cross-reference manifest."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        plan_id = data.get("plan_id", path.stem.replace("_xrefs", ""))
        refs = data.get("standards_refs", [])
        count = 0

        for ref in refs:
            raw_spec = ref.get("spec", "")
            spec = _clean_spec_number(raw_spec)
            if not spec or len(spec) < 4:
                continue

            release_str = ref.get("release", "")
            rel_num = _parse_release_num(release_str)

            key = (spec, rel_num)
            entry = agg[key]
            entry.ref_count += 1
            entry.source_plans.add(plan_id)
            count += 1

        return count

    def _collect_sections_from_tree(
        self, path: Path, agg: dict[tuple[str, int], _AccEntry]
    ) -> int:
        """Extract section-level references by scanning requirement text."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        plan_id = data.get("plan_id", path.stem.replace("_tree", ""))

        # Build a release mapping from the manifest-phase data:
        # For section-level refs, we inherit the release from the manifest
        # (since section refs in text typically don't repeat the release).
        # We map spec → release_num using whatever was already collected.
        spec_to_release: dict[str, int] = {}
        for (spec, rel_num) in agg:
            if plan_id in agg[(spec, rel_num)].source_plans and rel_num > 0:
                spec_to_release[spec] = rel_num

        count = 0
        for req in data.get("requirements", []):
            text = req.get("text", "")
            if not text:
                continue

            for pattern in _SECTION_PATTERNS:
                for match in pattern.finditer(text):
                    groups = match.groups()
                    # Pattern 1 (section of TS) has reversed groups
                    if pattern is _SECTION_PATTERNS[1]:
                        section_num, raw_spec = groups
                    else:
                        raw_spec, section_num = groups

                    spec = _clean_spec_number(raw_spec)
                    if not spec or len(spec) < 4:
                        continue

                    section_num = section_num.rstrip(".")
                    rel_num = spec_to_release.get(spec, 0)

                    key = (spec, rel_num)
                    entry = agg[key]
                    entry.sections.append(section_num)
                    entry.source_plans.add(plan_id)
                    entry.source_reqs.add(req.get("section_number", ""))
                    count += 1

        return count

    @staticmethod
    def _resolve_files(
        directory: Path | None,
        explicit: list[Path] | None,
        glob_pattern: str,
    ) -> list[Path]:
        if explicit:
            return explicit
        if directory and directory.exists():
            return sorted(directory.glob(glob_pattern))
        return []


class _AccEntry:
    """Accumulator for aggregating references."""
    __slots__ = ("ref_count", "source_plans", "sections", "source_reqs")

    def __init__(self):
        self.ref_count = 0
        self.source_plans: set[str] = set()
        self.sections: list[str] = []
        self.source_reqs: set[str] = set()
