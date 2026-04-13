"""Knowledge Graph builder (TDD 5.8).

Constructs a unified NetworkX DiGraph from:
- Parsed requirement trees   (data/parsed/*_tree.json)
- Cross-reference manifests  (data/resolved/*_xrefs.json)
- Feature taxonomy           (data/taxonomy/taxonomy.json)
- Standards reference index  (data/standards/reference_index.json)
- Extracted spec sections    (data/standards/TS_*/Rel-*/sections.json)

Construction follows TDD 5.8 sequence:
 1. MNO + Release nodes
 2. Plan nodes → linked to Release
 3. Requirement nodes → linked to Plan, parent-child hierarchy
 4. Cross-reference edges (internal depends_on, cross-plan depends_on)
 5. Standards nodes + edges (spec-level + section-level)
 6. Feature nodes + maps_to edges
 7. shared_standard edges (requirements sharing a standards section)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import networkx as nx

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

logger = logging.getLogger(__name__)


# ── Graph statistics ──────────────────────────────────────────────


@dataclass
class GraphStats:
    """Summary statistics for the constructed graph."""
    total_nodes: int = 0
    total_edges: int = 0
    nodes_by_type: dict[str, int] = field(default_factory=dict)
    edges_by_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


# ── Builder ───────────────────────────────────────────────────────


class KnowledgeGraphBuilder:
    """Builds a unified knowledge graph from all ingestion outputs."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self._std_ref_index: dict[str, list[str]] = defaultdict(list)
        # Maps std node id -> list of req node ids that reference it

    def build(
        self,
        trees_dir: Path,
        manifests_dir: Path,
        taxonomy_path: Path,
        standards_dir: Path,
    ) -> nx.DiGraph:
        """Build the full knowledge graph.

        Args:
            trees_dir: Directory with *_tree.json files
            manifests_dir: Directory with *_xrefs.json files
            taxonomy_path: Path to taxonomy.json
            standards_dir: Directory with reference_index.json and TS_*/Rel-*/
        """
        # Load all data
        trees = self._load_trees(trees_dir)
        manifests = self._load_manifests(manifests_dir)
        taxonomy = self._load_taxonomy(taxonomy_path)
        ref_index = self._load_reference_index(standards_dir)
        sections_map = self._load_extracted_sections(standards_dir)

        # Step 1-3: MNO, Release, Plan, Requirement nodes + hierarchy
        self._build_requirement_graph(trees)

        # Step 4: Cross-reference edges
        self._build_xref_edges(manifests)

        # Step 5: Standards nodes + edges
        self._build_standards_graph(ref_index, sections_map, manifests)

        # Step 6: Feature nodes + maps_to edges
        self._build_feature_graph(taxonomy, trees)

        # Step 7: shared_standard edges
        self._build_shared_standard_edges()

        stats = self.compute_stats()
        self._log_stats(stats)

        return self.graph

    # ── Data loading ──────────────────────────────────────────────

    def _load_trees(self, trees_dir: Path) -> list[dict]:
        trees = []
        for path in sorted(trees_dir.glob("*_tree.json")):
            with open(path, "r", encoding="utf-8") as f:
                trees.append(json.load(f))
        logger.info(f"Loaded {len(trees)} parsed trees from {trees_dir}")
        return trees

    def _load_manifests(self, manifests_dir: Path) -> dict[str, dict]:
        """Load manifests keyed by plan_id."""
        manifests = {}
        for path in sorted(manifests_dir.glob("*_xrefs.json")):
            with open(path, "r", encoding="utf-8") as f:
                m = json.load(f)
                manifests[m["plan_id"]] = m
        logger.info(f"Loaded {len(manifests)} xref manifests from {manifests_dir}")
        return manifests

    def _load_taxonomy(self, taxonomy_path: Path) -> dict | None:
        if not taxonomy_path.exists():
            logger.warning(f"Taxonomy not found at {taxonomy_path}")
            return None
        with open(taxonomy_path, "r", encoding="utf-8") as f:
            tax = json.load(f)
        logger.info(
            f"Loaded taxonomy with {len(tax.get('features', []))} features"
        )
        return tax

    def _load_reference_index(self, standards_dir: Path) -> dict | None:
        path = standards_dir / "reference_index.json"
        if not path.exists():
            logger.warning(f"Reference index not found at {path}")
            return None
        with open(path, "r", encoding="utf-8") as f:
            idx = json.load(f)
        logger.info(
            f"Loaded reference index: {idx.get('total_unique_specs', 0)} specs, "
            f"{idx.get('total_refs', 0)} refs"
        )
        return idx

    def _load_extracted_sections(
        self, standards_dir: Path
    ) -> dict[str, dict]:
        """Load extracted sections keyed by 'spec:release_num'."""
        sections_map = {}
        for sections_path in standards_dir.glob("TS_*/Rel-*/sections.json"):
            with open(sections_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            key = f"{data['spec_number']}:{data['release_num']}"
            sections_map[key] = data
        logger.info(
            f"Loaded {len(sections_map)} extracted section sets from {standards_dir}"
        )
        return sections_map

    # ── Step 1-3: Requirement graph ──────────────────────────────

    def _build_requirement_graph(self, trees: list[dict]) -> None:
        """Create MNO, Release, Plan, Requirement nodes and hierarchy edges."""
        mnos_seen: set[str] = set()
        releases_seen: set[str] = set()

        for tree in trees:
            mno = tree["mno"]
            release = tree["release"]
            pid = tree["plan_id"]

            # MNO node
            mid = mno_id(mno)
            if mno not in mnos_seen:
                self.graph.add_node(
                    mid,
                    node_type=NodeType.MNO.value,
                    mno=mno,
                    name=mno,
                )
                mnos_seen.add(mno)

            # Release node
            rid = release_id(mno, release)
            release_key = f"{mno}:{release}"
            if release_key not in releases_seen:
                self.graph.add_node(
                    rid,
                    node_type=NodeType.RELEASE.value,
                    mno=mno,
                    release=release,
                )
                self.graph.add_edge(
                    mid, rid, edge_type=EdgeType.HAS_RELEASE.value
                )
                releases_seen.add(release_key)

            # Plan node
            plid = plan_id(mno, release, pid)
            self.graph.add_node(
                plid,
                node_type=NodeType.PLAN.value,
                plan_id=pid,
                plan_name=tree.get("plan_name", ""),
                version=tree.get("version", ""),
                release_date=tree.get("release_date", ""),
                mno=mno,
                release=release,
            )
            self.graph.add_edge(
                rid, plid, edge_type=EdgeType.CONTAINS_PLAN.value
            )

            # Requirement nodes
            for r in tree.get("requirements", []):
                r_id = r.get("req_id", "")
                if not r_id:
                    continue

                rid_node = req_id(r_id)
                self.graph.add_node(
                    rid_node,
                    node_type=NodeType.REQUIREMENT.value,
                    req_id=r_id,
                    plan_id=pid,
                    mno=mno,
                    release=release,
                    section_number=r.get("section_number", ""),
                    title=r.get("title", ""),
                    text=r.get("text", ""),
                    zone_type=r.get("zone_type", ""),
                    hierarchy_path=r.get("hierarchy_path", []),
                )

                # belongs_to Plan
                self.graph.add_edge(
                    rid_node, plid, edge_type=EdgeType.BELONGS_TO.value
                )

                # parent_of hierarchy
                parent_id = r.get("parent_req_id", "")
                if parent_id:
                    parent_node = req_id(parent_id)
                    # Parent may not exist yet — add edge anyway,
                    # NetworkX auto-creates missing nodes but we'll
                    # only add the edge if parent is in this tree
                    if any(
                        rr.get("req_id") == parent_id
                        for rr in tree["requirements"]
                    ):
                        self.graph.add_edge(
                            parent_node,
                            rid_node,
                            edge_type=EdgeType.PARENT_OF.value,
                        )

        n_reqs = sum(
            1
            for _, d in self.graph.nodes(data=True)
            if d.get("node_type") == NodeType.REQUIREMENT.value
        )
        logger.info(
            f"Built requirement graph: {len(mnos_seen)} MNOs, "
            f"{len(releases_seen)} releases, {len(trees)} plans, "
            f"{n_reqs} requirements"
        )

    # ── Step 4: Cross-reference edges ────────────────────────────

    def _build_xref_edges(self, manifests: dict[str, dict]) -> None:
        """Create depends_on edges from resolved cross-references."""
        internal_count = 0
        cross_plan_count = 0

        for pid, manifest in manifests.items():
            # Internal refs → depends_on (within same plan)
            for ref in manifest.get("internal_refs", []):
                if ref.get("status") != "resolved":
                    continue
                src = req_id(ref["source_req_id"])
                tgt = req_id(ref["target_req_id"])
                if src in self.graph and tgt in self.graph:
                    # Don't duplicate parent_of edges as depends_on
                    if not self.graph.has_edge(src, tgt) and not self.graph.has_edge(tgt, src):
                        self.graph.add_edge(
                            src, tgt, edge_type=EdgeType.DEPENDS_ON.value,
                            ref_type="internal",
                        )
                        internal_count += 1

            # Cross-plan refs → depends_on (to other plans)
            for ref in manifest.get("cross_plan_refs", []):
                if ref.get("status") != "resolved":
                    continue
                src = req_id(ref["source_req_id"])
                # Cross-plan ref points to a plan, not a specific req.
                # Create edges to all reqs in the target plan.
                # But that would be noisy — instead create one edge
                # from the source req to the target plan node.
                target_plan = ref["target_plan_id"]
                # Find the plan node
                for nid, ndata in self.graph.nodes(data=True):
                    if (
                        ndata.get("node_type") == NodeType.PLAN.value
                        and ndata.get("plan_id") == target_plan
                    ):
                        if src in self.graph:
                            self.graph.add_edge(
                                src, nid,
                                edge_type=EdgeType.DEPENDS_ON.value,
                                ref_type="cross_plan",
                            )
                            cross_plan_count += 1
                        break

        logger.info(
            f"Cross-reference edges: {internal_count} internal, "
            f"{cross_plan_count} cross-plan"
        )

    # ── Step 5: Standards graph ──────────────────────────────────

    def _build_standards_graph(
        self,
        ref_index: dict | None,
        sections_map: dict[str, dict],
        manifests: dict[str, dict],
    ) -> None:
        """Create Standard_Section nodes and references_standard edges."""
        if not ref_index:
            logger.warning("No reference index — skipping standards graph")
            return

        # Create spec-level and section-level nodes from extracted sections
        for key, data in sections_map.items():
            spec = data["spec_number"]
            rel_num = data["release_num"]

            # Spec-level node
            spec_nid = std_spec_id(spec, rel_num)
            if spec_nid not in self.graph:
                self.graph.add_node(
                    spec_nid,
                    node_type=NodeType.STANDARD_SECTION.value,
                    spec=spec,
                    release_num=rel_num,
                    section="",
                    title=data.get("spec_title", ""),
                    version=data.get("version", ""),
                )

            # Referenced sections
            for sec in data.get("referenced_sections", []):
                sec_nid = std_section_id(spec, rel_num, sec["number"])
                self.graph.add_node(
                    sec_nid,
                    node_type=NodeType.STANDARD_SECTION.value,
                    spec=spec,
                    release_num=rel_num,
                    section=sec["number"],
                    title=sec.get("title", ""),
                    text=sec.get("text", ""),
                    depth=sec.get("depth", 0),
                )
                # parent_section edge
                self.graph.add_edge(
                    spec_nid, sec_nid,
                    edge_type=EdgeType.PARENT_SECTION.value,
                )

            # Context sections (parent/sibling/definitions)
            for sec in data.get("context_sections", []):
                sec_nid = std_section_id(spec, rel_num, sec["number"])
                if sec_nid not in self.graph:
                    self.graph.add_node(
                        sec_nid,
                        node_type=NodeType.STANDARD_SECTION.value,
                        spec=spec,
                        release_num=rel_num,
                        section=sec["number"],
                        title=sec.get("title", ""),
                        text=sec.get("text", ""),
                        depth=sec.get("depth", 0),
                    )
                    self.graph.add_edge(
                        spec_nid, sec_nid,
                        edge_type=EdgeType.PARENT_SECTION.value,
                    )

        # Create references_standard edges from xref manifests
        std_edge_count = 0
        for pid, manifest in manifests.items():
            for ref in manifest.get("standards_refs", []):
                if ref.get("status") != "resolved":
                    continue

                src = req_id(ref["source_req_id"])
                if src not in self.graph:
                    continue

                spec = ref["spec"].replace("3GPP TS ", "").rstrip(".")
                release_str = ref.get("release", "")
                rel_num = self._parse_release_num(release_str)

                section = ref.get("section", "")

                if section and rel_num > 0:
                    tgt = std_section_id(spec, rel_num, section)
                elif rel_num > 0:
                    tgt = std_spec_id(spec, rel_num)
                else:
                    # No release info — create a generic spec node
                    tgt = std_spec_id(spec, 0)

                # Ensure target node exists
                if tgt not in self.graph:
                    self.graph.add_node(
                        tgt,
                        node_type=NodeType.STANDARD_SECTION.value,
                        spec=spec,
                        release_num=rel_num,
                        section=section,
                    )

                self.graph.add_edge(
                    src, tgt,
                    edge_type=EdgeType.REFERENCES_STANDARD.value,
                    release=release_str,
                    release_source=ref.get("release_source", ""),
                )
                std_edge_count += 1

                # Track for shared_standard computation
                self._std_ref_index[tgt].append(src)

        # Also add section-level refs from the reference index
        # (these come from tree text scanning, not xref manifests)
        for spec_entry in ref_index.get("specs", []):
            spec = spec_entry["spec"]
            rel_num = spec_entry.get("release_num", 0)
            sections = spec_entry.get("sections", [])
            source_plans = spec_entry.get("source_plans", [])

            for section in sections:
                sec_nid = std_section_id(spec, rel_num, section)
                if sec_nid not in self.graph:
                    self.graph.add_node(
                        sec_nid,
                        node_type=NodeType.STANDARD_SECTION.value,
                        spec=spec,
                        release_num=rel_num,
                        section=section,
                    )

        n_std = sum(
            1
            for _, d in self.graph.nodes(data=True)
            if d.get("node_type") == NodeType.STANDARD_SECTION.value
        )
        logger.info(
            f"Standards graph: {n_std} standard nodes, "
            f"{std_edge_count} references_standard edges"
        )

    # ── Step 6: Feature graph ────────────────────────────────────

    def _build_feature_graph(
        self, taxonomy: dict | None, trees: list[dict]
    ) -> None:
        """Create Feature nodes and maps_to edges."""
        if not taxonomy:
            logger.warning("No taxonomy — skipping feature graph")
            return

        features = taxonomy.get("features", [])

        # Build plan_id -> set of req_ids for mapping
        plan_reqs: dict[str, set[str]] = {}
        for tree in trees:
            pid = tree["plan_id"]
            plan_reqs[pid] = {
                r["req_id"]
                for r in tree.get("requirements", [])
                if r.get("req_id")
            }

        maps_to_count = 0
        dep_count = 0

        # Pass 1: Create all feature nodes
        for feat in features:
            fid = feat["feature_id"]
            fnid = feature_id(fid)

            self.graph.add_node(
                fnid,
                node_type=NodeType.FEATURE.value,
                feature_id=fid,
                name=feat.get("name", ""),
                description=feat.get("description", ""),
                keywords=feat.get("keywords", []),
                mno_coverage=feat.get("mno_coverage", {}),
                source_plans=feat.get("source_plans", []),
            )

        # Pass 2: Create maps_to and feature_depends_on edges
        for feat in features:
            fid = feat["feature_id"]
            fnid = feature_id(fid)

            # maps_to: connect requirements from primary plans
            # A feature is primary in certain plans — all reqs in those
            # plans map to this feature. This is a coarse mapping that
            # a real LLM would refine to specific requirement subsets.
            primary_plans = feat.get("is_primary_in", [])
            referenced_plans = feat.get("is_referenced_in", [])

            for p in primary_plans:
                for rid in plan_reqs.get(p, []):
                    rnode = req_id(rid)
                    if rnode in self.graph:
                        self.graph.add_edge(
                            rnode, fnid,
                            edge_type=EdgeType.MAPS_TO.value,
                            mapping_type="primary",
                        )
                        maps_to_count += 1

            for p in referenced_plans:
                for rid in plan_reqs.get(p, []):
                    rnode = req_id(rid)
                    if rnode in self.graph:
                        self.graph.add_edge(
                            rnode, fnid,
                            edge_type=EdgeType.MAPS_TO.value,
                            mapping_type="referenced",
                        )
                        maps_to_count += 1

            # feature_depends_on
            for dep_fid in feat.get("depends_on_features", []):
                dep_fnid = feature_id(dep_fid)
                if dep_fnid in self.graph:
                    self.graph.add_edge(
                        fnid, dep_fnid,
                        edge_type=EdgeType.FEATURE_DEPENDS_ON.value,
                    )
                    dep_count += 1

        logger.info(
            f"Feature graph: {len(features)} features, "
            f"{maps_to_count} maps_to edges, "
            f"{dep_count} feature_depends_on edges"
        )

    # ── Step 7: Shared standard edges ────────────────────────────

    def _build_shared_standard_edges(self) -> None:
        """Create shared_standard edges between reqs that reference the same standard."""
        shared_count = 0
        for std_nid, req_nodes in self._std_ref_index.items():
            if len(req_nodes) < 2:
                continue
            # Create edges between all pairs (undirected semantics,
            # but stored as directed — add both directions)
            for i, r1 in enumerate(req_nodes):
                for r2 in req_nodes[i + 1:]:
                    # Only link reqs from different plans
                    r1_plan = self.graph.nodes[r1].get("plan_id", "")
                    r2_plan = self.graph.nodes[r2].get("plan_id", "")
                    if r1_plan != r2_plan:
                        if not self.graph.has_edge(r1, r2):
                            self.graph.add_edge(
                                r1, r2,
                                edge_type=EdgeType.SHARED_STANDARD.value,
                                shared_standard=std_nid,
                            )
                            shared_count += 1

        logger.info(f"Shared standard edges: {shared_count}")

    # ── Stats ─────────────────────────────────────────────────────

    def compute_stats(self) -> GraphStats:
        """Compute summary statistics for the graph."""
        nodes_by_type: dict[str, int] = defaultdict(int)
        for _, data in self.graph.nodes(data=True):
            nt = data.get("node_type", "unknown")
            nodes_by_type[nt] += 1

        edges_by_type: dict[str, int] = defaultdict(int)
        for _, _, data in self.graph.edges(data=True):
            et = data.get("edge_type", "unknown")
            edges_by_type[et] += 1

        return GraphStats(
            total_nodes=self.graph.number_of_nodes(),
            total_edges=self.graph.number_of_edges(),
            nodes_by_type=dict(nodes_by_type),
            edges_by_type=dict(edges_by_type),
        )

    def _log_stats(self, stats: GraphStats) -> None:
        logger.info(f"\n{'=' * 50}")
        logger.info(f"Knowledge Graph: {stats.total_nodes} nodes, {stats.total_edges} edges")
        logger.info(f"{'=' * 50}")
        logger.info("Nodes by type:")
        for nt, count in sorted(stats.nodes_by_type.items()):
            logger.info(f"  {nt:<20s} {count:>6d}")
        logger.info("Edges by type:")
        for et, count in sorted(stats.edges_by_type.items()):
            logger.info(f"  {et:<20s} {count:>6d}")

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _parse_release_num(release_str: str) -> int:
        """Extract numeric release from 'Release 11', 'Rel-15', etc."""
        import re
        m = re.search(r"(\d+)", release_str)
        return int(m.group(1)) if m else 0

    # ── Serialization ─────────────────────────────────────────────

    def save_graphml(self, path: Path) -> None:
        """Save the graph in GraphML format."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # GraphML doesn't support list attributes — convert to strings
        g_copy = self.graph.copy()
        for nid in g_copy.nodes():
            for k, v in list(g_copy.nodes[nid].items()):
                if isinstance(v, (list, dict)):
                    g_copy.nodes[nid][k] = json.dumps(v)
        for u, v in g_copy.edges():
            for k, val in list(g_copy.edges[u, v].items()):
                if isinstance(val, (list, dict)):
                    g_copy.edges[u, v][k] = json.dumps(val)
        nx.write_graphml(g_copy, str(path))
        logger.info(f"Saved graph to {path}")

    def save_json(self, path: Path) -> None:
        """Save the graph as a node-link JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self.graph)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Saved graph JSON to {path}")
