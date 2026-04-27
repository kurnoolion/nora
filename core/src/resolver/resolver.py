"""Cross-reference resolver (TDD 5.5, Methods 1 & 2).

Resolves references between parsed requirement trees:
- Internal: req ID references within the same document
- Cross-plan: references to other plans within the same MNO/release
- Standards: 3GPP/GSMA citations with release version resolution

Deterministic only — no LLM. Method 3 (concept linking) is handled
by the feature taxonomy step.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

from src.parser.structural_parser import RequirementTree, Requirement, StandardsRef

logger = logging.getLogger(__name__)


# ── Data model ─────────────────────────────────────────────────────


class RefStatus(str, Enum):
    RESOLVED = "resolved"
    BROKEN = "broken"  # target ID/section exists in corpus but not found
    UNRESOLVED = "unresolved"  # target plan/spec not in corpus


@dataclass
class ResolvedInternalRef:
    """A resolved reference to another requirement in the same plan."""
    source_req_id: str
    source_section: str
    target_req_id: str
    target_section: str = ""
    target_title: str = ""
    status: RefStatus = RefStatus.RESOLVED


@dataclass
class ResolvedCrossPlanRef:
    """A resolved reference to a requirement in another plan."""
    source_req_id: str
    source_section: str
    target_plan_id: str
    target_req_ids: list[str] = field(default_factory=list)
    status: RefStatus = RefStatus.RESOLVED


@dataclass
class ResolvedStandardsRef:
    """A resolved standards reference with release version."""
    source_req_id: str
    source_section: str
    spec: str
    section: str = ""
    release: str = ""
    release_source: str = ""  # "inline" (from text) or "doc_level" (from mapping)
    status: RefStatus = RefStatus.RESOLVED


@dataclass
class ManifestSummary:
    total_internal: int = 0
    resolved_internal: int = 0
    broken_internal: int = 0
    total_cross_plan: int = 0
    resolved_cross_plan: int = 0
    unresolved_cross_plan: int = 0
    total_standards: int = 0
    resolved_standards: int = 0
    unresolved_standards: int = 0


@dataclass
class CrossReferenceManifest:
    """All resolved outbound references for a single document."""
    plan_id: str = ""
    mno: str = ""
    release: str = ""
    internal_refs: list[ResolvedInternalRef] = field(default_factory=list)
    cross_plan_refs: list[ResolvedCrossPlanRef] = field(default_factory=list)
    standards_refs: list[ResolvedStandardsRef] = field(default_factory=list)
    summary: ManifestSummary = field(default_factory=ManifestSummary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


# ── Resolver ───────────────────────────────────────────────────────


class CrossReferenceResolver:
    """Resolve cross-references across a corpus of parsed requirement trees."""

    def __init__(self, trees: list[RequirementTree]):
        self._trees = trees
        # Build lookup indexes
        self._tree_by_plan: dict[str, RequirementTree] = {}
        self._req_by_id: dict[str, tuple[str, Requirement]] = {}  # req_id -> (plan_id, req)

        for tree in trees:
            self._tree_by_plan[tree.plan_id] = tree
            for req in tree.requirements:
                if req.req_id:
                    self._req_by_id[req.req_id] = (tree.plan_id, req)

        logger.info(
            f"Resolver initialized: {len(trees)} trees, "
            f"{len(self._req_by_id)} indexed requirements"
        )

    def resolve_all(self) -> list[CrossReferenceManifest]:
        """Resolve cross-references for all trees in the corpus."""
        manifests = []
        for tree in self._trees:
            manifest = self.resolve_tree(tree)
            manifests.append(manifest)
        return manifests

    def resolve_tree(self, tree: RequirementTree) -> CrossReferenceManifest:
        """Resolve all cross-references for a single tree."""
        manifest = CrossReferenceManifest(
            plan_id=tree.plan_id,
            mno=tree.mno,
            release=tree.release,
        )

        for req in tree.requirements:
            xr = req.cross_references

            # Internal refs
            for target_id in xr.internal:
                ref = self._resolve_internal(req, target_id, tree)
                manifest.internal_refs.append(ref)

            # Cross-plan refs
            for target_plan in xr.external_plans:
                ref = self._resolve_cross_plan(req, target_plan)
                manifest.cross_plan_refs.append(ref)

            # Standards refs
            for std_ref in xr.standards:
                ref = self._resolve_standards(req, std_ref, tree)
                manifest.standards_refs.append(ref)

        manifest.summary = self._compute_summary(manifest)
        self._log_manifest(manifest)
        return manifest

    # ── Internal references ────────────────────────────────────────

    def _resolve_internal(
        self, source: Requirement, target_id: str, tree: RequirementTree
    ) -> ResolvedInternalRef:
        """Resolve a reference to another requirement in the same plan."""
        # Look up the target in the same tree
        for req in tree.requirements:
            if req.req_id == target_id:
                return ResolvedInternalRef(
                    source_req_id=source.req_id,
                    source_section=source.section_number,
                    target_req_id=target_id,
                    target_section=req.section_number,
                    target_title=req.title,
                    status=RefStatus.RESOLVED,
                )

        # Target not found in this tree — might be in another plan
        # (parser classified it as internal but it could be a different plan)
        if target_id in self._req_by_id:
            other_plan, other_req = self._req_by_id[target_id]
            return ResolvedInternalRef(
                source_req_id=source.req_id,
                source_section=source.section_number,
                target_req_id=target_id,
                target_section=other_req.section_number,
                target_title=f"[actually in {other_plan}] {other_req.title}",
                status=RefStatus.RESOLVED,
            )

        return ResolvedInternalRef(
            source_req_id=source.req_id,
            source_section=source.section_number,
            target_req_id=target_id,
            status=RefStatus.BROKEN,
        )

    # ── Cross-plan references ──────────────────────────────────────

    def _resolve_cross_plan(
        self, source: Requirement, target_plan: str
    ) -> ResolvedCrossPlanRef:
        """Resolve a reference to another plan."""
        if target_plan in self._tree_by_plan:
            # Plan exists in corpus — find all req IDs that reference
            # this source's text (we just confirm the plan is loadable)
            target_tree = self._tree_by_plan[target_plan]
            return ResolvedCrossPlanRef(
                source_req_id=source.req_id,
                source_section=source.section_number,
                target_plan_id=target_plan,
                target_req_ids=[r.req_id for r in target_tree.requirements if r.req_id],
                status=RefStatus.RESOLVED,
            )

        return ResolvedCrossPlanRef(
            source_req_id=source.req_id,
            source_section=source.section_number,
            target_plan_id=target_plan,
            status=RefStatus.UNRESOLVED,
        )

    # ── Standards references ───────────────────────────────────────

    def _resolve_standards(
        self, source: Requirement, std_ref: StandardsRef, tree: RequirementTree
    ) -> ResolvedStandardsRef:
        """Resolve a standards reference, filling in release from doc-level mapping."""
        release = std_ref.release
        release_source = "inline" if release else ""

        if not release:
            # Try doc-level mapping
            doc_release = tree.referenced_standards_releases.get(std_ref.spec, "")
            if doc_release:
                release = doc_release
                release_source = "doc_level"

        status = RefStatus.RESOLVED if release else RefStatus.UNRESOLVED

        return ResolvedStandardsRef(
            source_req_id=source.req_id,
            source_section=source.section_number,
            spec=std_ref.spec,
            section=std_ref.section,
            release=release,
            release_source=release_source,
            status=status,
        )

    # ── Summary ────────────────────────────────────────────────────

    @staticmethod
    def _compute_summary(manifest: CrossReferenceManifest) -> ManifestSummary:
        resolved_int = sum(1 for r in manifest.internal_refs if r.status == RefStatus.RESOLVED)
        broken_int = sum(1 for r in manifest.internal_refs if r.status == RefStatus.BROKEN)
        resolved_xp = sum(1 for r in manifest.cross_plan_refs if r.status == RefStatus.RESOLVED)
        unresolved_xp = sum(1 for r in manifest.cross_plan_refs if r.status == RefStatus.UNRESOLVED)
        resolved_std = sum(1 for r in manifest.standards_refs if r.status == RefStatus.RESOLVED)
        unresolved_std = sum(1 for r in manifest.standards_refs if r.status == RefStatus.UNRESOLVED)

        return ManifestSummary(
            total_internal=len(manifest.internal_refs),
            resolved_internal=resolved_int,
            broken_internal=broken_int,
            total_cross_plan=len(manifest.cross_plan_refs),
            resolved_cross_plan=resolved_xp,
            unresolved_cross_plan=unresolved_xp,
            total_standards=len(manifest.standards_refs),
            resolved_standards=resolved_std,
            unresolved_standards=unresolved_std,
        )

    @staticmethod
    def _log_manifest(manifest: CrossReferenceManifest) -> None:
        s = manifest.summary
        logger.info(
            f"{manifest.plan_id}: "
            f"internal={s.resolved_internal}/{s.total_internal} "
            f"(broken={s.broken_internal}), "
            f"cross-plan={s.resolved_cross_plan}/{s.total_cross_plan} "
            f"(unresolved={s.unresolved_cross_plan}), "
            f"standards={s.resolved_standards}/{s.total_standards} "
            f"(unresolved={s.unresolved_standards})"
        )
