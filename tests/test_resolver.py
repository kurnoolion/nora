"""Tests for the cross-reference resolver."""

from pathlib import Path

import pytest

from src.parser.structural_parser import (
    CrossReferences,
    Requirement,
    RequirementTree,
    StandardsRef,
)
from src.resolver.resolver import (
    CrossReferenceManifest,
    CrossReferenceResolver,
    RefStatus,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_req(
    req_id: str,
    section: str,
    title: str = "",
    internal: list[str] | None = None,
    external_plans: list[str] | None = None,
    standards: list[StandardsRef] | None = None,
) -> Requirement:
    return Requirement(
        req_id=req_id,
        section_number=section,
        title=title or section,
        cross_references=CrossReferences(
            internal=internal or [],
            external_plans=external_plans or [],
            standards=standards or [],
        ),
    )


def _make_tree(
    plan_id: str,
    reqs: list[Requirement],
    std_releases: dict[str, str] | None = None,
) -> RequirementTree:
    return RequirementTree(
        plan_id=plan_id,
        mno="TEST",
        release="2026",
        referenced_standards_releases=std_releases or {},
        requirements=reqs,
    )


# ── Unit tests ─────────────────────────────────────────────────────


class TestInternalResolution:
    def test_resolved_when_target_exists(self):
        tree = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", internal=["REQ_A_2"]),
            _make_req("REQ_A_2", "1.2"),
        ])
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        assert len(manifest.internal_refs) == 1
        ref = manifest.internal_refs[0]
        assert ref.status == RefStatus.RESOLVED
        assert ref.target_section == "1.2"

    def test_broken_when_target_missing(self):
        tree = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", internal=["REQ_A_999"]),
        ])
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        assert len(manifest.internal_refs) == 1
        assert manifest.internal_refs[0].status == RefStatus.BROKEN

    def test_resolved_cross_tree_when_misclassified(self):
        """If parser classified a ref as internal but target is in another tree."""
        tree_a = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", internal=["REQ_B_1"]),
        ])
        tree_b = _make_tree("PLAN_B", [
            _make_req("REQ_B_1", "1.1", title="Target in B"),
        ])
        resolver = CrossReferenceResolver([tree_a, tree_b])
        manifest = resolver.resolve_tree(tree_a)

        ref = manifest.internal_refs[0]
        assert ref.status == RefStatus.RESOLVED
        assert "PLAN_B" in ref.target_title

    def test_multiple_refs_from_one_requirement(self):
        tree = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", internal=["REQ_A_2", "REQ_A_3"]),
            _make_req("REQ_A_2", "1.2"),
            _make_req("REQ_A_3", "1.3"),
        ])
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        assert len(manifest.internal_refs) == 2
        assert all(r.status == RefStatus.RESOLVED for r in manifest.internal_refs)


class TestCrossPlanResolution:
    def test_resolved_when_plan_in_corpus(self):
        tree_a = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", external_plans=["PLAN_B"]),
        ])
        tree_b = _make_tree("PLAN_B", [
            _make_req("REQ_B_1", "1.1"),
            _make_req("REQ_B_2", "1.2"),
        ])
        resolver = CrossReferenceResolver([tree_a, tree_b])
        manifest = resolver.resolve_tree(tree_a)

        assert len(manifest.cross_plan_refs) == 1
        ref = manifest.cross_plan_refs[0]
        assert ref.status == RefStatus.RESOLVED
        assert ref.target_plan_id == "PLAN_B"
        assert len(ref.target_req_ids) == 2

    def test_unresolved_when_plan_not_in_corpus(self):
        tree_a = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", external_plans=["PLAN_X"]),
        ])
        resolver = CrossReferenceResolver([tree_a])
        manifest = resolver.resolve_tree(tree_a)

        ref = manifest.cross_plan_refs[0]
        assert ref.status == RefStatus.UNRESOLVED
        assert ref.target_plan_id == "PLAN_X"
        assert ref.target_req_ids == []


class TestStandardsResolution:
    def test_inline_release_preserved(self):
        tree = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", standards=[
                StandardsRef(spec="3GPP TS 24.301", section="5.5.1", release="Release 10"),
            ]),
        ])
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        ref = manifest.standards_refs[0]
        assert ref.status == RefStatus.RESOLVED
        assert ref.release == "Release 10"
        assert ref.release_source == "inline"

    def test_doc_level_release_fills_missing(self):
        tree = _make_tree(
            "PLAN_A",
            [_make_req("REQ_A_1", "1.1", standards=[
                StandardsRef(spec="3GPP TS 24.301", section="5.5.1", release=""),
            ])],
            std_releases={"3GPP TS 24.301": "Release 10"},
        )
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        ref = manifest.standards_refs[0]
        assert ref.status == RefStatus.RESOLVED
        assert ref.release == "Release 10"
        assert ref.release_source == "doc_level"

    def test_unresolved_when_no_release_anywhere(self):
        tree = _make_tree("PLAN_A", [
            _make_req("REQ_A_1", "1.1", standards=[
                StandardsRef(spec="3GPP TS 99.999", section="1.1", release=""),
            ]),
        ])
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        ref = manifest.standards_refs[0]
        assert ref.status == RefStatus.UNRESOLVED
        assert ref.release == ""

    def test_inline_release_takes_precedence(self):
        """If both inline and doc-level exist, inline wins."""
        tree = _make_tree(
            "PLAN_A",
            [_make_req("REQ_A_1", "1.1", standards=[
                StandardsRef(spec="3GPP TS 24.301", section="5.5.1", release="Release 15"),
            ])],
            std_releases={"3GPP TS 24.301": "Release 10"},
        )
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        ref = manifest.standards_refs[0]
        assert ref.release == "Release 15"
        assert ref.release_source == "inline"


class TestSummary:
    def test_summary_counts(self):
        tree_a = _make_tree(
            "PLAN_A",
            [
                _make_req("REQ_A_1", "1.1",
                          internal=["REQ_A_2", "REQ_A_999"],
                          external_plans=["PLAN_X"],
                          standards=[
                              StandardsRef(spec="3GPP TS 24.301", release="Release 10"),
                              StandardsRef(spec="3GPP TS 99.999"),
                          ]),
                _make_req("REQ_A_2", "1.2"),
            ],
            std_releases={},
        )
        resolver = CrossReferenceResolver([tree_a])
        manifest = resolver.resolve_tree(tree_a)
        s = manifest.summary

        assert s.total_internal == 2
        assert s.resolved_internal == 1
        assert s.broken_internal == 1
        assert s.total_cross_plan == 1
        assert s.unresolved_cross_plan == 1
        assert s.total_standards == 2
        assert s.resolved_standards == 1
        assert s.unresolved_standards == 1


class TestManifestRoundTrip:
    def test_save_and_load(self, tmp_path: Path):
        tree = _make_tree(
            "PLAN_A",
            [
                _make_req("REQ_A_1", "1.1",
                          internal=["REQ_A_2"],
                          standards=[StandardsRef(spec="3GPP TS 24.301", release="Release 10")]),
                _make_req("REQ_A_2", "1.2"),
            ],
            std_releases={"3GPP TS 24.301": "Release 10"},
        )
        resolver = CrossReferenceResolver([tree])
        manifest = resolver.resolve_tree(tree)

        path = tmp_path / "manifest.json"
        manifest.save_json(path)

        import json
        loaded = json.loads(path.read_text())
        assert loaded["plan_id"] == "PLAN_A"
        assert loaded["summary"]["total_internal"] == 1
        assert loaded["summary"]["resolved_internal"] == 1
        assert len(loaded["internal_refs"]) == 1
        assert loaded["internal_refs"][0]["status"] == "resolved"


# ── Pipeline test against real data ────────────────────────────────


class TestResolverPipeline:
    """Run resolver against real parsed trees."""

    pytestmark = pytest.mark.skipif(
        not Path("data/parsed/LTEDATARETRY_tree.json").exists(),
        reason="Parsed tree data not available",
    )

    @pytest.fixture(scope="class")
    def manifests(self) -> list[CrossReferenceManifest]:
        tree_files = sorted(Path("data/parsed").glob("*_tree.json"))
        trees = [RequirementTree.load_json(f) for f in tree_files]
        resolver = CrossReferenceResolver(trees)
        return resolver.resolve_all()

    def test_produces_manifest_per_tree(self, manifests):
        assert len(manifests) == 5

    def test_all_plans_present(self, manifests):
        plan_ids = {m.plan_id for m in manifests}
        assert plan_ids == {"LTEAT", "LTEB13NAC", "LTEDATARETRY", "LTEOTADM", "LTESMS"}

    def test_lteb13nac_cross_plan_resolved(self, manifests):
        """LTEB13NAC references LTESMS which is in our corpus."""
        m = next(m for m in manifests if m.plan_id == "LTEB13NAC")
        resolved_xp = [r for r in m.cross_plan_refs if r.status == RefStatus.RESOLVED]
        assert len(resolved_xp) >= 1
        assert any(r.target_plan_id == "LTESMS" for r in resolved_xp)

    def test_lteotadm_cross_plan_unresolved(self, manifests):
        """LTEOTADM references MMOTADM/ODOTADM which aren't in corpus."""
        m = next(m for m in manifests if m.plan_id == "LTEOTADM")
        unresolved_xp = [r for r in m.cross_plan_refs if r.status == RefStatus.UNRESOLVED]
        assert len(unresolved_xp) >= 2
        unresolved_plans = {r.target_plan_id for r in unresolved_xp}
        assert "MMOTADM" in unresolved_plans

    def test_standards_release_resolution(self, manifests):
        """LTEDATARETRY has doc-level standards releases — refs should resolve."""
        m = next(m for m in manifests if m.plan_id == "LTEDATARETRY")
        resolved_std = [r for r in m.standards_refs if r.status == RefStatus.RESOLVED]
        assert len(resolved_std) > 50
        doc_level = [r for r in resolved_std if r.release_source == "doc_level"]
        assert len(doc_level) > 0

    def test_lteat_standards_all_unresolved(self, manifests):
        """LTEAT has no doc-level release mapping — standards should be unresolved."""
        m = next(m for m in manifests if m.plan_id == "LTEAT")
        assert m.summary.total_standards > 0
        assert m.summary.unresolved_standards == m.summary.total_standards

    def test_no_negative_counts_in_summary(self, manifests):
        for m in manifests:
            s = m.summary
            assert s.resolved_internal >= 0
            assert s.broken_internal >= 0
            assert s.resolved_internal + s.broken_internal == s.total_internal
            assert s.resolved_cross_plan + s.unresolved_cross_plan == s.total_cross_plan
            assert s.resolved_standards + s.unresolved_standards == s.total_standards
