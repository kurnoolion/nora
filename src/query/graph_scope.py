"""Graph scoper (TDD 7.3).

Uses the knowledge graph to identify candidate requirement nodes
by combining:
  1. Entity lookup (req IDs, plan names) — direct graph node match
  2. Feature lookup — feature nodes → maps_to → requirements
  3. Edge traversal — depends_on, references_standard, shared_standard,
     parent_of (upward for context)

All lookups are scoped by MNO/release from Stage 2.
Traversal depth is configurable.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from src.graph.schema import NodeType, EdgeType
from src.query.schema import (
    ScopedQuery,
    QueryType,
    CandidateSet,
    CandidateNode,
    MNOScope,
)

logger = logging.getLogger(__name__)

# Default traversal depth limits by query type
_DEFAULT_DEPTH: dict[QueryType, int] = {
    QueryType.SINGLE_DOC: 1,
    QueryType.CROSS_DOC: 2,
    QueryType.CROSS_MNO_COMPARISON: 2,
    QueryType.FEATURE_LEVEL: 2,
    QueryType.STANDARDS_COMPARISON: 2,
    QueryType.RELEASE_DIFF: 1,
    QueryType.TRACEABILITY: 2,
    QueryType.GENERAL: 2,
}

# Edge types to traverse by query type
_TRAVERSAL_EDGES: dict[QueryType, list[str]] = {
    QueryType.SINGLE_DOC: [
        EdgeType.DEPENDS_ON.value,
        EdgeType.PARENT_OF.value,
        EdgeType.REFERENCES_STANDARD.value,
    ],
    QueryType.CROSS_DOC: [
        EdgeType.DEPENDS_ON.value,
        EdgeType.PARENT_OF.value,
        EdgeType.REFERENCES_STANDARD.value,
        EdgeType.SHARED_STANDARD.value,
    ],
    QueryType.CROSS_MNO_COMPARISON: [
        EdgeType.MAPS_TO.value,
        EdgeType.REFERENCES_STANDARD.value,
    ],
    QueryType.FEATURE_LEVEL: [
        EdgeType.MAPS_TO.value,
        EdgeType.DEPENDS_ON.value,
        EdgeType.REFERENCES_STANDARD.value,
    ],
    QueryType.STANDARDS_COMPARISON: [
        EdgeType.REFERENCES_STANDARD.value,
        EdgeType.PARENT_SECTION.value,
    ],
    QueryType.GENERAL: [
        EdgeType.DEPENDS_ON.value,
        EdgeType.PARENT_OF.value,
        EdgeType.REFERENCES_STANDARD.value,
        EdgeType.SHARED_STANDARD.value,
        EdgeType.MAPS_TO.value,
    ],
}


class GraphScoper:
    """Scopes candidate nodes using knowledge graph traversal."""

    def __init__(self, graph: nx.DiGraph, max_depth: int | None = None) -> None:
        self._graph = graph
        self._max_depth_override = max_depth

    def scope(self, scoped_query: ScopedQuery) -> CandidateSet:
        """Find candidate nodes for the query.

        Process:
        1. Entity lookup — direct node match for req IDs, plan IDs
        2. Feature lookup — follow maps_to from feature nodes
        3. Edge traversal — expand from seed nodes
        """
        intent = scoped_query.intent
        qt = intent.query_type
        scopes = scoped_query.scoped_mnos

        max_depth = self._max_depth_override or _DEFAULT_DEPTH.get(qt, 2)
        allowed_edges = _TRAVERSAL_EDGES.get(qt, _TRAVERSAL_EDGES[QueryType.GENERAL])

        # Collect seed requirement nodes
        seed_nodes: dict[str, CandidateNode] = {}

        # 1. Entity lookup
        entity_nodes = self._entity_lookup(intent, scopes)
        for n in entity_nodes:
            seed_nodes[n.node_id] = n

        # 2. Feature lookup
        feature_nodes_found = self._feature_lookup(intent, scopes)
        for n in feature_nodes_found:
            seed_nodes[n.node_id] = n

        # 3. Plan-level lookup (if plan_ids specified but no specific entities found)
        if intent.plan_ids and not seed_nodes:
            plan_nodes = self._plan_lookup(intent.plan_ids, scopes)
            for n in plan_nodes:
                seed_nodes[n.node_id] = n

        # 4. If still nothing, use concept-based text search on node titles
        if not seed_nodes and (intent.concepts or intent.entities):
            title_nodes = self._title_search(intent, scopes)
            for n in title_nodes:
                seed_nodes[n.node_id] = n

        # 5. Edge traversal to expand candidates
        traversed = self._traverse(
            seed_nodes, max_depth, allowed_edges, scopes
        )

        # Partition into node types
        candidates = CandidateSet()
        all_found = {**seed_nodes, **traversed}

        for nid, cn in all_found.items():
            if cn.node_type == NodeType.REQUIREMENT.value:
                candidates.requirement_nodes.append(cn)
            elif cn.node_type == NodeType.STANDARD_SECTION.value:
                candidates.standards_nodes.append(cn)
            elif cn.node_type == NodeType.FEATURE.value:
                candidates.feature_nodes.append(cn)

        logger.info(
            f"Graph scoping: {len(seed_nodes)} seeds → "
            f"{candidates.total} candidates "
            f"({len(candidates.requirement_nodes)} reqs, "
            f"{len(candidates.standards_nodes)} stds, "
            f"{len(candidates.feature_nodes)} features) "
            f"depth={max_depth}"
        )
        return candidates

    # ── Entity lookup ────────────────────────────────────────────

    def _entity_lookup(
        self, intent, scopes: list[MNOScope]
    ) -> list[CandidateNode]:
        """Look up nodes by entity names (req IDs, etc.)."""
        nodes = []

        for entity in intent.entities:
            # Try as req_id
            req_nid = f"req:{entity}"
            if req_nid in self._graph:
                data = self._graph.nodes[req_nid]
                if self._in_scope(data, scopes):
                    nodes.append(CandidateNode(
                        node_id=req_nid,
                        node_type=data.get("node_type", ""),
                        score=1.0,
                        source="entity",
                        attributes=dict(data),
                    ))

        return nodes

    # ── Feature lookup ───────────────────────────────────────────

    def _feature_lookup(
        self, intent, scopes: list[MNOScope]
    ) -> list[CandidateNode]:
        """Look up requirements via feature nodes."""
        nodes = []
        seen = set()

        for fid in intent.likely_features:
            fnid = f"feature:{fid}"
            if fnid not in self._graph:
                continue

            # Follow maps_to edges backwards (reqs → feature)
            for pred in self._graph.predecessors(fnid):
                edge_data = self._graph.edges[pred, fnid]
                if edge_data.get("edge_type") != EdgeType.MAPS_TO.value:
                    continue

                if pred in seen:
                    continue

                data = self._graph.nodes[pred]
                if (
                    data.get("node_type") == NodeType.REQUIREMENT.value
                    and self._in_scope(data, scopes)
                ):
                    nodes.append(CandidateNode(
                        node_id=pred,
                        node_type=data.get("node_type", ""),
                        score=0.8,
                        source="feature",
                        attributes=dict(data),
                    ))
                    seen.add(pred)

        return nodes

    # ── Plan lookup ──────────────────────────────────────────────

    def _plan_lookup(
        self, plan_ids: list[str], scopes: list[MNOScope]
    ) -> list[CandidateNode]:
        """Get all requirements belonging to specified plans."""
        nodes = []
        seen = set()

        for nid, data in self._graph.nodes(data=True):
            if data.get("node_type") != NodeType.REQUIREMENT.value:
                continue
            if data.get("plan_id") not in plan_ids:
                continue
            if not self._in_scope(data, scopes):
                continue
            if nid in seen:
                continue

            nodes.append(CandidateNode(
                node_id=nid,
                node_type=data.get("node_type", ""),
                score=0.6,
                source="plan",
                attributes=dict(data),
            ))
            seen.add(nid)

        return nodes

    # ── Title search ─────────────────────────────────────────────

    def _title_search(
        self, intent, scopes: list[MNOScope]
    ) -> list[CandidateNode]:
        """Search node titles/text for concepts and entities."""
        nodes = []
        seen = set()

        search_terms = [t.lower() for t in intent.concepts + intent.entities]
        if not search_terms:
            return nodes

        for nid, data in self._graph.nodes(data=True):
            if data.get("node_type") != NodeType.REQUIREMENT.value:
                continue
            if not self._in_scope(data, scopes):
                continue
            if nid in seen:
                continue

            title = data.get("title", "").lower()
            text = data.get("text", "").lower()
            searchable = f"{title} {text}"

            matches = sum(1 for term in search_terms if term in searchable)
            if matches > 0:
                nodes.append(CandidateNode(
                    node_id=nid,
                    node_type=data.get("node_type", ""),
                    score=0.3 + 0.2 * min(matches, 3),
                    source="title_search",
                    attributes=dict(data),
                ))
                seen.add(nid)

        return nodes

    # ── Edge traversal ───────────────────────────────────────────

    def _traverse(
        self,
        seed_nodes: dict[str, CandidateNode],
        max_depth: int,
        allowed_edges: list[str],
        scopes: list[MNOScope],
    ) -> dict[str, CandidateNode]:
        """Traverse from seed nodes along allowed edge types."""
        discovered: dict[str, CandidateNode] = {}
        frontier = set(seed_nodes.keys())
        visited = set(seed_nodes.keys())

        for depth in range(max_depth):
            next_frontier: set[str] = set()
            score_decay = 0.7 ** (depth + 1)

            for nid in frontier:
                # Follow outgoing edges
                for _, target, edata in self._graph.out_edges(nid, data=True):
                    etype = edata.get("edge_type", "")
                    if etype not in allowed_edges:
                        continue
                    if target in visited:
                        continue

                    tdata = self._graph.nodes[target]
                    ttype = tdata.get("node_type", "")

                    # Scope check for requirement nodes
                    if ttype == NodeType.REQUIREMENT.value:
                        if not self._in_scope(tdata, scopes):
                            continue

                    discovered[target] = CandidateNode(
                        node_id=target,
                        node_type=ttype,
                        score=score_decay,
                        source=f"traversal:{etype}",
                        attributes=dict(tdata),
                    )
                    visited.add(target)
                    next_frontier.add(target)

                # Follow incoming edges (for parent_of traversal upward,
                # and maps_to which points req→feature)
                for source, _, edata in self._graph.in_edges(nid, data=True):
                    etype = edata.get("edge_type", "")
                    if etype not in allowed_edges:
                        continue
                    if source in visited:
                        continue

                    sdata = self._graph.nodes[source]
                    stype = sdata.get("node_type", "")

                    if stype == NodeType.REQUIREMENT.value:
                        if not self._in_scope(sdata, scopes):
                            continue

                    discovered[source] = CandidateNode(
                        node_id=source,
                        node_type=stype,
                        score=score_decay,
                        source=f"traversal:{etype}",
                        attributes=dict(sdata),
                    )
                    visited.add(source)
                    next_frontier.add(source)

            frontier = next_frontier
            if not frontier:
                break

        return discovered

    # ── Scope filtering ──────────────────────────────────────────

    @staticmethod
    def _in_scope(node_data: dict[str, Any], scopes: list[MNOScope]) -> bool:
        """Check if a node is within the resolved MNO/release scope."""
        if not scopes:
            return True

        node_mno = node_data.get("mno", "")
        node_release = node_data.get("release", "")

        for scope in scopes:
            if node_mno == scope.mno and node_release == scope.release:
                return True

        return False
