"""Tests for the Knowledge Graph construction (Step 8).

Covers:
- Schema: node/edge ID generation
- Builder: synthetic data construction, all edge types
- Integration: building from real parsed data
- Verification queries: connectivity, traversals
"""

import json
from pathlib import Path

import networkx as nx
import pytest

from src.graph.schema import (
    NodeType,
    EdgeType,
    mno_id,
    release_id,
    plan_id,
    req_id,
    std_section_id,
    std_spec_id,
    feature_id,
)
from src.graph.builder import KnowledgeGraphBuilder, GraphStats


# ── Schema Tests ─────────────────────────────────────────────────


class TestSchema:
    def test_mno_id(self):
        assert mno_id("VZW") == "mno:VZW"

    def test_release_id(self):
        assert release_id("VZW", "2026_feb") == "release:VZW:2026_feb"

    def test_plan_id(self):
        assert plan_id("VZW", "2026_feb", "LTESMS") == "plan:VZW:2026_feb:LTESMS"

    def test_req_id(self):
        assert req_id("VZ_REQ_LTESMS_123") == "req:VZ_REQ_LTESMS_123"

    def test_std_section_id(self):
        assert std_section_id("24.301", 11, "5.5.1") == "std:24.301:11:5.5.1"

    def test_std_spec_id(self):
        assert std_spec_id("24.301", 11) == "std:24.301:11"

    def test_feature_id(self):
        assert feature_id("IMS_REGISTRATION") == "feature:IMS_REGISTRATION"

    def test_ids_are_globally_unique(self):
        """Different node types with same base should produce different IDs."""
        ids = [
            mno_id("VZW"),
            release_id("VZW", "2026"),
            plan_id("VZW", "2026", "P1"),
            req_id("VZW"),
            feature_id("VZW"),
        ]
        assert len(set(ids)) == len(ids)

    def test_node_type_enum_values(self):
        assert NodeType.MNO.value == "MNO"
        assert NodeType.REQUIREMENT.value == "Requirement"
        assert NodeType.STANDARD_SECTION.value == "Standard_Section"
        assert NodeType.FEATURE.value == "Feature"

    def test_edge_type_enum_values(self):
        assert EdgeType.HAS_RELEASE.value == "has_release"
        assert EdgeType.PARENT_OF.value == "parent_of"
        assert EdgeType.REFERENCES_STANDARD.value == "references_standard"
        assert EdgeType.MAPS_TO.value == "maps_to"


# ── Builder Unit Tests (Synthetic Data) ──────────────────────────


def _make_tree(
    mno="VZW", release="2026_feb", plan_id_str="PLAN_A",
    reqs=None,
):
    """Create a minimal parsed tree dict."""
    if reqs is None:
        reqs = [
            {
                "req_id": f"REQ_{plan_id_str}_1",
                "section_number": "1.1",
                "title": "First req",
                "parent_req_id": "",
                "parent_section": "",
                "hierarchy_path": ["1", "1.1"],
                "zone_type": "specs",
                "text": "The device shall do X.",
                "tables": [],
                "images": [],
                "children": [f"REQ_{plan_id_str}_2"],
                "cross_references": {
                    "internal": [],
                    "external_plans": [],
                    "standards": [],
                },
            },
            {
                "req_id": f"REQ_{plan_id_str}_2",
                "section_number": "1.1.1",
                "title": "Child req",
                "parent_req_id": f"REQ_{plan_id_str}_1",
                "parent_section": "1.1",
                "hierarchy_path": ["1", "1.1", "1.1.1"],
                "zone_type": "specs",
                "text": "Sub-requirement.",
                "tables": [],
                "images": [],
                "children": [],
                "cross_references": {
                    "internal": [],
                    "external_plans": [],
                    "standards": [],
                },
            },
        ]
    return {
        "mno": mno,
        "release": release,
        "plan_id": plan_id_str,
        "plan_name": f"Test Plan {plan_id_str}",
        "version": "1",
        "release_date": "2026-02-01",
        "referenced_standards_releases": {},
        "requirements": reqs,
    }


def _make_manifest(plan_id_str="PLAN_A", internal=None, cross_plan=None, standards=None):
    """Create a minimal xref manifest dict."""
    return {
        "plan_id": plan_id_str,
        "mno": "VZW",
        "release": "2026_feb",
        "internal_refs": internal or [],
        "cross_plan_refs": cross_plan or [],
        "standards_refs": standards or [],
        "summary": {},
    }


def _make_taxonomy(features=None):
    """Create a minimal taxonomy dict."""
    if features is None:
        features = [
            {
                "feature_id": "FEAT_A",
                "name": "Feature A",
                "description": "Test feature",
                "keywords": ["test"],
                "mno_coverage": {"VZW": ["2026_feb"]},
                "source_plans": ["PLAN_A"],
                "depends_on_features": [],
                "is_primary_in": ["PLAN_A"],
                "is_referenced_in": [],
            }
        ]
    return {
        "mno": "VZW",
        "release": "2026_feb",
        "features": features,
        "source_documents": ["PLAN_A"],
    }


def _make_ref_index(specs=None):
    """Create a minimal reference index dict."""
    return {
        "specs": specs or [],
        "total_refs": 0,
        "total_unique_specs": 0,
        "source_documents": [],
    }


class TestBuilderRequirementGraph:
    def _build_from_trees(self, trees):
        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph(trees)
        return builder

    def test_single_tree_creates_mno_release_plan(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph

        mno_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "MNO"]
        assert len(mno_nodes) == 1
        assert mno_nodes[0] == "mno:VZW"

        release_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "Release"]
        assert len(release_nodes) == 1

        plan_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "Plan"]
        assert len(plan_nodes) == 1

    def test_has_release_edge(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        assert g.has_edge("mno:VZW", "release:VZW:2026_feb")
        edge = g.edges["mno:VZW", "release:VZW:2026_feb"]
        assert edge["edge_type"] == "has_release"

    def test_contains_plan_edge(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        assert g.has_edge("release:VZW:2026_feb", "plan:VZW:2026_feb:PLAN_A")

    def test_requirement_nodes(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        req_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "Requirement"]
        assert len(req_nodes) == 2

    def test_belongs_to_edge(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        assert g.has_edge("req:REQ_PLAN_A_1", "plan:VZW:2026_feb:PLAN_A")
        edge = g.edges["req:REQ_PLAN_A_1", "plan:VZW:2026_feb:PLAN_A"]
        assert edge["edge_type"] == "belongs_to"

    def test_parent_of_edge(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        assert g.has_edge("req:REQ_PLAN_A_1", "req:REQ_PLAN_A_2")
        edge = g.edges["req:REQ_PLAN_A_1", "req:REQ_PLAN_A_2"]
        assert edge["edge_type"] == "parent_of"

    def test_multiple_trees_same_mno(self):
        trees = [_make_tree(plan_id_str="P1"), _make_tree(plan_id_str="P2")]
        b = self._build_from_trees(trees)
        g = b.graph

        # Only one MNO node
        mno_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "MNO"]
        assert len(mno_nodes) == 1

        # Two plan nodes
        plan_nodes = [n for n, d in g.nodes(data=True) if d.get("node_type") == "Plan"]
        assert len(plan_nodes) == 2

    def test_requirement_attributes(self):
        b = self._build_from_trees([_make_tree()])
        g = b.graph
        data = g.nodes["req:REQ_PLAN_A_1"]
        assert data["req_id"] == "REQ_PLAN_A_1"
        assert data["section_number"] == "1.1"
        assert data["title"] == "First req"
        assert data["mno"] == "VZW"
        assert data["plan_id"] == "PLAN_A"

    def test_empty_req_id_skipped(self):
        tree = _make_tree(reqs=[
            {"req_id": "", "section_number": "1.1", "title": "No ID",
             "parent_req_id": "", "parent_section": "",
             "hierarchy_path": [], "zone_type": "", "text": "",
             "tables": [], "images": [], "children": [],
             "cross_references": {"internal": [], "external_plans": [], "standards": []}},
        ])
        b = self._build_from_trees([tree])
        req_nodes = [n for n, d in b.graph.nodes(data=True) if d.get("node_type") == "Requirement"]
        assert len(req_nodes) == 0


class TestBuilderXrefEdges:
    def test_internal_depends_on(self):
        tree = _make_tree()
        manifest = _make_manifest(internal=[{
            "source_req_id": "REQ_PLAN_A_1",
            "source_section": "1.1",
            "target_req_id": "REQ_PLAN_A_2",
            "target_section": "1.1.1",
            "target_title": "Child req",
            "status": "resolved",
        }])

        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([tree])
        builder._build_xref_edges({"PLAN_A": manifest})
        g = builder.graph

        # parent_of already exists between these two, so depends_on should NOT be added
        edges_between = [
            d for _, _, d in g.edges(data=True)
            if d.get("edge_type") == "depends_on"
        ]
        # The builder skips depends_on if parent_of already exists
        assert len(edges_between) == 0

    def test_internal_depends_on_non_parent(self):
        """depends_on created when not already a parent_of edge."""
        reqs = [
            {"req_id": "R1", "section_number": "1.1", "title": "A",
             "parent_req_id": "", "parent_section": "",
             "hierarchy_path": [], "zone_type": "", "text": "",
             "tables": [], "images": [], "children": [],
             "cross_references": {"internal": [], "external_plans": [], "standards": []}},
            {"req_id": "R2", "section_number": "2.1", "title": "B",
             "parent_req_id": "", "parent_section": "",
             "hierarchy_path": [], "zone_type": "", "text": "",
             "tables": [], "images": [], "children": [],
             "cross_references": {"internal": [], "external_plans": [], "standards": []}},
        ]
        tree = _make_tree(reqs=reqs)
        manifest = _make_manifest(internal=[{
            "source_req_id": "R1",
            "source_section": "1.1",
            "target_req_id": "R2",
            "target_section": "2.1",
            "target_title": "B",
            "status": "resolved",
        }])

        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([tree])
        builder._build_xref_edges({"PLAN_A": manifest})

        assert builder.graph.has_edge("req:R1", "req:R2")
        edge = builder.graph.edges["req:R1", "req:R2"]
        assert edge["edge_type"] == "depends_on"

    def test_broken_ref_skipped(self):
        tree = _make_tree()
        manifest = _make_manifest(internal=[{
            "source_req_id": "REQ_PLAN_A_1",
            "source_section": "1.1",
            "target_req_id": "REQ_NONEXISTENT",
            "target_section": "",
            "target_title": "",
            "status": "broken",
        }])

        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([tree])
        builder._build_xref_edges({"PLAN_A": manifest})

        depends_on = [
            d for _, _, d in builder.graph.edges(data=True)
            if d.get("edge_type") == "depends_on"
        ]
        assert len(depends_on) == 0


class TestBuilderStandardsGraph:
    def test_standards_nodes_from_sections(self):
        builder = KnowledgeGraphBuilder()
        tree = _make_tree()
        builder._build_requirement_graph([tree])

        sections_map = {
            "24.301:11": {
                "spec_number": "24.301",
                "release_num": 11,
                "version": "11.14.0",
                "spec_title": "NAS",
                "referenced_sections": [
                    {"number": "5.5.1", "title": "Attach", "text": "...", "depth": 3},
                ],
                "context_sections": [
                    {"number": "3.1", "title": "Definitions", "text": "...", "depth": 2},
                ],
                "total_sections_in_spec": 744,
                "source_plans": ["PLAN_A"],
            }
        }
        manifest = _make_manifest(standards=[{
            "source_req_id": "REQ_PLAN_A_1",
            "source_section": "1.1",
            "spec": "3GPP TS 24.301.",
            "section": "",
            "release": "Release 11",
            "release_source": "doc_level",
            "status": "resolved",
        }])
        ref_index = _make_ref_index()

        builder._build_standards_graph(ref_index, sections_map, {"PLAN_A": manifest})

        # Should have spec-level, referenced, and context section nodes
        std_nodes = [
            n for n, d in builder.graph.nodes(data=True)
            if d.get("node_type") == "Standard_Section"
        ]
        assert len(std_nodes) >= 3  # spec + 5.5.1 + 3.1

    def test_references_standard_edge(self):
        builder = KnowledgeGraphBuilder()
        tree = _make_tree()
        builder._build_requirement_graph([tree])

        manifest = _make_manifest(standards=[{
            "source_req_id": "REQ_PLAN_A_1",
            "source_section": "1.1",
            "spec": "24.301",
            "section": "",
            "release": "Release 11",
            "release_source": "doc_level",
            "status": "resolved",
        }])

        builder._build_standards_graph(_make_ref_index(), {}, {"PLAN_A": manifest})

        ref_edges = [
            (u, v, d) for u, v, d in builder.graph.edges(data=True)
            if d.get("edge_type") == "references_standard"
        ]
        assert len(ref_edges) == 1
        assert ref_edges[0][0] == "req:REQ_PLAN_A_1"

    def test_parent_section_edges(self):
        builder = KnowledgeGraphBuilder()
        sections_map = {
            "24.301:11": {
                "spec_number": "24.301",
                "release_num": 11,
                "version": "11.14.0",
                "spec_title": "NAS",
                "referenced_sections": [
                    {"number": "5.5.1", "title": "Attach", "text": "...", "depth": 3},
                ],
                "context_sections": [],
                "total_sections_in_spec": 744,
                "source_plans": [],
            }
        }

        builder._build_standards_graph(_make_ref_index(), sections_map, {})

        assert builder.graph.has_edge("std:24.301:11", "std:24.301:11:5.5.1")
        edge = builder.graph.edges["std:24.301:11", "std:24.301:11:5.5.1"]
        assert edge["edge_type"] == "parent_section"


class TestBuilderFeatureGraph:
    def test_feature_nodes_created(self):
        builder = KnowledgeGraphBuilder()
        tree = _make_tree()
        builder._build_requirement_graph([tree])

        taxonomy = _make_taxonomy()
        builder._build_feature_graph(taxonomy, [tree])

        feat_nodes = [
            n for n, d in builder.graph.nodes(data=True)
            if d.get("node_type") == "Feature"
        ]
        assert len(feat_nodes) == 1
        assert "feature:FEAT_A" in [n for n in feat_nodes]

    def test_maps_to_edges(self):
        builder = KnowledgeGraphBuilder()
        tree = _make_tree()
        builder._build_requirement_graph([tree])

        taxonomy = _make_taxonomy()
        builder._build_feature_graph(taxonomy, [tree])

        maps_to = [
            (u, v, d) for u, v, d in builder.graph.edges(data=True)
            if d.get("edge_type") == "maps_to"
        ]
        assert len(maps_to) == 2  # 2 reqs in PLAN_A
        for u, v, d in maps_to:
            assert v == "feature:FEAT_A"
            assert d["mapping_type"] == "primary"

    def test_feature_depends_on(self):
        builder = KnowledgeGraphBuilder()
        tree = _make_tree()
        builder._build_requirement_graph([tree])

        taxonomy = _make_taxonomy(features=[
            {
                "feature_id": "FEAT_A", "name": "A", "description": "",
                "keywords": [], "mno_coverage": {},
                "source_plans": ["PLAN_A"], "depends_on_features": ["FEAT_B"],
                "is_primary_in": [], "is_referenced_in": [],
            },
            {
                "feature_id": "FEAT_B", "name": "B", "description": "",
                "keywords": [], "mno_coverage": {},
                "source_plans": ["PLAN_A"], "depends_on_features": [],
                "is_primary_in": [], "is_referenced_in": [],
            },
        ])
        builder._build_feature_graph(taxonomy, [tree])

        assert builder.graph.has_edge("feature:FEAT_A", "feature:FEAT_B")
        edge = builder.graph.edges["feature:FEAT_A", "feature:FEAT_B"]
        assert edge["edge_type"] == "feature_depends_on"

    def test_no_taxonomy_skips(self):
        builder = KnowledgeGraphBuilder()
        builder._build_feature_graph(None, [])
        feat_nodes = [
            n for n, d in builder.graph.nodes(data=True)
            if d.get("node_type") == "Feature"
        ]
        assert len(feat_nodes) == 0


class TestBuilderSharedStandard:
    def test_shared_standard_cross_plan(self):
        """Reqs from different plans referencing same standard get linked."""
        builder = KnowledgeGraphBuilder()
        trees = [_make_tree(plan_id_str="P1"), _make_tree(plan_id_str="P2")]
        builder._build_requirement_graph(trees)

        manifests = {
            "P1": _make_manifest("P1", standards=[{
                "source_req_id": "REQ_P1_1",
                "source_section": "1.1",
                "spec": "24.301",
                "section": "",
                "release": "Release 11",
                "release_source": "doc_level",
                "status": "resolved",
            }]),
            "P2": _make_manifest("P2", standards=[{
                "source_req_id": "REQ_P2_1",
                "source_section": "1.1",
                "spec": "24.301",
                "section": "",
                "release": "Release 11",
                "release_source": "doc_level",
                "status": "resolved",
            }]),
        }

        builder._build_standards_graph(_make_ref_index(), {}, manifests)
        builder._build_shared_standard_edges()

        shared = [
            (u, v, d) for u, v, d in builder.graph.edges(data=True)
            if d.get("edge_type") == "shared_standard"
        ]
        assert len(shared) == 1

    def test_no_shared_standard_same_plan(self):
        """Reqs from the same plan don't get shared_standard edges."""
        builder = KnowledgeGraphBuilder()
        reqs = [
            {"req_id": "R1", "section_number": "1", "title": "A",
             "parent_req_id": "", "parent_section": "",
             "hierarchy_path": [], "zone_type": "", "text": "",
             "tables": [], "images": [], "children": [],
             "cross_references": {"internal": [], "external_plans": [], "standards": []}},
            {"req_id": "R2", "section_number": "2", "title": "B",
             "parent_req_id": "", "parent_section": "",
             "hierarchy_path": [], "zone_type": "", "text": "",
             "tables": [], "images": [], "children": [],
             "cross_references": {"internal": [], "external_plans": [], "standards": []}},
        ]
        tree = _make_tree(reqs=reqs)
        builder._build_requirement_graph([tree])

        manifest = _make_manifest(standards=[
            {"source_req_id": "R1", "source_section": "1",
             "spec": "24.301", "section": "", "release": "Release 11",
             "release_source": "doc_level", "status": "resolved"},
            {"source_req_id": "R2", "source_section": "2",
             "spec": "24.301", "section": "", "release": "Release 11",
             "release_source": "doc_level", "status": "resolved"},
        ])

        builder._build_standards_graph(_make_ref_index(), {}, {"PLAN_A": manifest})
        builder._build_shared_standard_edges()

        shared = [
            d for _, _, d in builder.graph.edges(data=True)
            if d.get("edge_type") == "shared_standard"
        ]
        assert len(shared) == 0


class TestGraphStats:
    def test_compute_stats(self):
        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([_make_tree()])
        stats = builder.compute_stats()

        assert stats.total_nodes > 0
        assert stats.total_edges > 0
        assert "MNO" in stats.nodes_by_type
        assert "Requirement" in stats.nodes_by_type
        assert "has_release" in stats.edges_by_type

    def test_stats_round_trip(self, tmp_path):
        stats = GraphStats(
            total_nodes=100,
            total_edges=200,
            nodes_by_type={"MNO": 1, "Requirement": 50},
            edges_by_type={"parent_of": 40},
        )
        path = tmp_path / "stats.json"
        stats.save_json(path)

        with open(path) as f:
            loaded = json.load(f)
        assert loaded["total_nodes"] == 100
        assert loaded["nodes_by_type"]["MNO"] == 1


class TestBuilderSerialization:
    def test_save_json(self, tmp_path):
        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([_make_tree()])
        path = tmp_path / "graph.json"
        builder.save_json(path)
        assert path.exists()

        with open(path) as f:
            data = json.load(f)
        assert "nodes" in data or "directed" in data  # node-link format

    def test_save_graphml(self, tmp_path):
        builder = KnowledgeGraphBuilder()
        builder._build_requirement_graph([_make_tree()])
        path = tmp_path / "graph.graphml"
        builder.save_graphml(path)
        assert path.exists()

        # Can load back
        loaded = nx.read_graphml(str(path))
        assert loaded.number_of_nodes() == builder.graph.number_of_nodes()


# ── Full Build Test (Synthetic) ──────────────────────────────────


class TestFullBuild:
    def test_full_build_synthetic(self, tmp_path):
        """End-to-end build with synthetic data files."""
        # Create trees
        trees_dir = tmp_path / "parsed"
        trees_dir.mkdir()
        for pid in ["P1", "P2"]:
            tree = _make_tree(plan_id_str=pid)
            with open(trees_dir / f"{pid}_tree.json", "w") as f:
                json.dump(tree, f)

        # Create manifests
        manifests_dir = tmp_path / "resolved"
        manifests_dir.mkdir()
        for pid in ["P1", "P2"]:
            manifest = _make_manifest(pid, standards=[{
                "source_req_id": f"REQ_{pid}_1",
                "source_section": "1.1",
                "spec": "24.301",
                "section": "",
                "release": "Release 11",
                "release_source": "doc_level",
                "status": "resolved",
            }])
            with open(manifests_dir / f"{pid}_xrefs.json", "w") as f:
                json.dump(manifest, f)

        # Create taxonomy (must reference P1 to get maps_to edges)
        taxonomy_path = tmp_path / "taxonomy.json"
        tax = _make_taxonomy(features=[{
            "feature_id": "FEAT_A", "name": "Feature A", "description": "Test",
            "keywords": ["test"], "mno_coverage": {"VZW": ["2026_feb"]},
            "source_plans": ["P1"], "depends_on_features": [],
            "is_primary_in": ["P1"], "is_referenced_in": [],
        }])
        with open(taxonomy_path, "w") as f:
            json.dump(tax, f)

        # Create standards dir with reference index
        standards_dir = tmp_path / "standards"
        standards_dir.mkdir()
        with open(standards_dir / "reference_index.json", "w") as f:
            json.dump(_make_ref_index(), f)

        # Build
        builder = KnowledgeGraphBuilder()
        graph = builder.build(
            trees_dir=trees_dir,
            manifests_dir=manifests_dir,
            taxonomy_path=taxonomy_path,
            standards_dir=standards_dir,
        )

        stats = builder.compute_stats()

        # Verify counts
        assert stats.nodes_by_type["MNO"] == 1
        assert stats.nodes_by_type["Release"] == 1
        assert stats.nodes_by_type["Plan"] == 2
        assert stats.nodes_by_type["Requirement"] == 4  # 2 per plan
        assert stats.nodes_by_type.get("Feature", 0) == 1
        assert stats.nodes_by_type.get("Standard_Section", 0) >= 1

        # Verify edge types present
        assert "has_release" in stats.edges_by_type
        assert "contains_plan" in stats.edges_by_type
        assert "belongs_to" in stats.edges_by_type
        assert "parent_of" in stats.edges_by_type
        assert "references_standard" in stats.edges_by_type
        assert "maps_to" in stats.edges_by_type
        assert "shared_standard" in stats.edges_by_type

    def test_missing_taxonomy_still_builds(self, tmp_path):
        """Graph builds without taxonomy — just no feature nodes."""
        trees_dir = tmp_path / "parsed"
        trees_dir.mkdir()
        tree = _make_tree()
        with open(trees_dir / "P1_tree.json", "w") as f:
            json.dump(tree, f)

        manifests_dir = tmp_path / "resolved"
        manifests_dir.mkdir()
        manifest = _make_manifest()
        with open(manifests_dir / "P1_xrefs.json", "w") as f:
            json.dump(manifest, f)

        standards_dir = tmp_path / "standards"
        standards_dir.mkdir()
        with open(standards_dir / "reference_index.json", "w") as f:
            json.dump(_make_ref_index(), f)

        builder = KnowledgeGraphBuilder()
        graph = builder.build(
            trees_dir=trees_dir,
            manifests_dir=manifests_dir,
            taxonomy_path=tmp_path / "nonexistent.json",
            standards_dir=standards_dir,
        )

        stats = builder.compute_stats()
        assert stats.nodes_by_type.get("Feature", 0) == 0
        assert stats.nodes_by_type["Requirement"] == 2


# ── Integration Tests (Real Data) ────────────────────────────────


TREES_DIR = Path("data/parsed")
MANIFESTS_DIR = Path("data/resolved")
TAXONOMY_PATH = Path("data/taxonomy/taxonomy.json")
STANDARDS_DIR = Path("data/standards")


@pytest.mark.skipif(
    not TREES_DIR.exists() or not MANIFESTS_DIR.exists(),
    reason="Parsed/resolved data not available",
)
class TestIntegration:
    @pytest.fixture(scope="class")
    def built_graph(self):
        builder = KnowledgeGraphBuilder()
        graph = builder.build(
            trees_dir=TREES_DIR,
            manifests_dir=MANIFESTS_DIR,
            taxonomy_path=TAXONOMY_PATH,
            standards_dir=STANDARDS_DIR,
        )
        return builder, graph

    def test_node_count(self, built_graph):
        builder, graph = built_graph
        assert graph.number_of_nodes() > 700  # at least reqs

    def test_has_all_node_types(self, built_graph):
        _, graph = built_graph
        types = {d.get("node_type") for _, d in graph.nodes(data=True)}
        assert "MNO" in types
        assert "Release" in types
        assert "Plan" in types
        assert "Requirement" in types
        assert "Standard_Section" in types
        assert "Feature" in types

    def test_five_plans(self, built_graph):
        _, graph = built_graph
        plans = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "Plan"]
        assert len(plans) == 5

    def test_requirement_count_matches_trees(self, built_graph):
        _, graph = built_graph
        reqs = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "Requirement"]
        assert len(reqs) == 705

    def test_features_count(self, built_graph):
        _, graph = built_graph
        feats = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "Feature"]
        assert len(feats) == 16

    def test_standards_nodes_exist(self, built_graph):
        _, graph = built_graph
        stds = [n for n, d in graph.nodes(data=True) if d.get("node_type") == "Standard_Section"]
        assert len(stds) > 100

    def test_graph_mostly_connected(self, built_graph):
        _, graph = built_graph
        undirected = graph.to_undirected()
        components = list(nx.connected_components(undirected))
        largest = max(components, key=len)
        # At least 95% in one component
        assert len(largest) / graph.number_of_nodes() > 0.95

    def test_can_traverse_plan_to_standard(self, built_graph):
        """Can reach a standard node from a plan node."""
        _, graph = built_graph
        plan_node = "plan:VZW:2026_feb:LTEDATARETRY"
        undirected = graph.to_undirected()

        # Find any standard node
        std_nodes = [n for n, d in graph.nodes(data=True)
                     if d.get("node_type") == "Standard_Section"
                     and d.get("spec") == "24.301"]
        assert len(std_nodes) > 0

        # Should be reachable
        reachable = nx.node_connected_component(undirected, plan_node)
        assert any(s in reachable for s in std_nodes)

    def test_shared_standard_edges_exist(self, built_graph):
        _, graph = built_graph
        shared = [d for _, _, d in graph.edges(data=True)
                  if d.get("edge_type") == "shared_standard"]
        assert len(shared) > 0

    def test_references_standard_edges_exist(self, built_graph):
        _, graph = built_graph
        refs = [d for _, _, d in graph.edges(data=True)
                if d.get("edge_type") == "references_standard"]
        assert len(refs) > 200

    def test_stats(self, built_graph):
        builder, _ = built_graph
        stats = builder.compute_stats()
        assert stats.total_nodes > 1000
        assert stats.total_edges > 5000
