"""CLI entry point for Knowledge Graph construction (TDD 5.8).

Usage:
    # Build graph from all data directories
    python -m src.graph.graph_cli \
        --trees-dir data/parsed \
        --manifests-dir data/resolved \
        --taxonomy data/taxonomy/taxonomy.json \
        --standards-dir data/standards \
        --output-dir data/graph

    # Run verification queries
    python -m src.graph.graph_cli \
        --trees-dir data/parsed \
        --manifests-dir data/resolved \
        --taxonomy data/taxonomy/taxonomy.json \
        --standards-dir data/standards \
        --output-dir data/graph \
        --verify

Output files:
    data/graph/knowledge_graph.json    — node-link JSON serialization
    data/graph/graph_stats.json        — summary statistics
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import networkx as nx

from src.graph.builder import KnowledgeGraphBuilder
from src.graph.schema import NodeType, EdgeType


def main():
    parser = argparse.ArgumentParser(
        description="Build the unified knowledge graph from ingestion outputs."
    )
    parser.add_argument(
        "--trees-dir", type=Path, default=Path("data/parsed"),
        help="Directory containing *_tree.json files. Default: data/parsed",
    )
    parser.add_argument(
        "--manifests-dir", type=Path, default=Path("data/resolved"),
        help="Directory containing *_xrefs.json files. Default: data/resolved",
    )
    parser.add_argument(
        "--taxonomy", type=Path, default=Path("data/taxonomy/taxonomy.json"),
        help="Path to unified taxonomy JSON. Default: data/taxonomy/taxonomy.json",
    )
    parser.add_argument(
        "--standards-dir", type=Path, default=Path("data/standards"),
        help="Directory with reference_index.json and TS_*/. Default: data/standards",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/graph"),
        help="Output directory. Default: data/graph",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Run verification queries after building",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    t0 = time.time()

    # Build
    builder = KnowledgeGraphBuilder()
    graph = builder.build(
        trees_dir=args.trees_dir,
        manifests_dir=args.manifests_dir,
        taxonomy_path=args.taxonomy,
        standards_dir=args.standards_dir,
    )

    # Save outputs
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    builder.save_json(output_dir / "knowledge_graph.json")
    stats = builder.compute_stats()
    stats.save_json(output_dir / "graph_stats.json")

    elapsed = time.time() - t0
    logging.info(f"\nGraph built in {elapsed:.1f}s")
    logging.info(f"Output: {output_dir}/")

    # Verification queries
    if args.verify:
        _run_verification(graph)


def _run_verification(graph: nx.DiGraph) -> None:
    """Run diagnostic queries to verify graph correctness."""
    logging.info(f"\n{'=' * 60}")
    logging.info("Verification Queries")
    logging.info(f"{'=' * 60}")

    # Q1: Requirements per plan
    logging.info("\n--- Requirements per plan ---")
    plan_counts: dict[str, int] = defaultdict(int)
    for nid, data in graph.nodes(data=True):
        if data.get("node_type") == NodeType.REQUIREMENT.value:
            plan_counts[data.get("plan_id", "?")] += 1
    for plan, count in sorted(plan_counts.items()):
        logging.info(f"  {plan:<20s} {count:>5d} requirements")

    # Q2: Feature coverage — which features span the most plans
    logging.info("\n--- Feature coverage ---")
    for nid, data in graph.nodes(data=True):
        if data.get("node_type") == NodeType.FEATURE.value:
            plans = data.get("source_plans", [])
            in_edges = [
                (u, d)
                for u, _, d in graph.in_edges(nid, data=True)
                if d.get("edge_type") == EdgeType.MAPS_TO.value
            ]
            logging.info(
                f"  {data.get('feature_id', '?'):<25s} "
                f"{len(plans)} plans, {len(in_edges)} req mappings"
            )

    # Q3: Standards with most requirement references
    logging.info("\n--- Most-referenced standards ---")
    std_ref_counts: dict[str, int] = defaultdict(int)
    for u, v, data in graph.edges(data=True):
        if data.get("edge_type") == EdgeType.REFERENCES_STANDARD.value:
            std_ref_counts[v] += 1
    top_stds = sorted(std_ref_counts.items(), key=lambda x: -x[1])[:10]
    for std_nid, count in top_stds:
        ndata = graph.nodes.get(std_nid, {})
        spec = ndata.get("spec", "?")
        sec = ndata.get("section", "")
        rel = ndata.get("release_num", "?")
        label = f"TS {spec} Rel-{rel}"
        if sec:
            label += f" §{sec}"
        logging.info(f"  {label:<40s} {count:>4d} refs")

    # Q4: Cross-plan dependencies
    logging.info("\n--- Cross-plan dependencies ---")
    cross_plan = [
        (u, v, d)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == EdgeType.DEPENDS_ON.value
        and d.get("ref_type") == "cross_plan"
    ]
    logging.info(f"  {len(cross_plan)} cross-plan dependency edges")

    # Q5: Shared standard connections (cross-document links via standards)
    logging.info("\n--- Shared standard connections ---")
    shared = [
        (u, v, d)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == EdgeType.SHARED_STANDARD.value
    ]
    logging.info(f"  {len(shared)} shared_standard edges")
    # Show plan pairs connected
    pair_counts: dict[tuple[str, str], int] = defaultdict(int)
    for u, v, _ in shared:
        p1 = graph.nodes[u].get("plan_id", "?")
        p2 = graph.nodes[v].get("plan_id", "?")
        pair = tuple(sorted([p1, p2]))
        pair_counts[pair] += 1
    for (p1, p2), count in sorted(pair_counts.items(), key=lambda x: -x[1]):
        logging.info(f"    {p1} <-> {p2}: {count} shared standards")

    # Q6: Graph connectivity
    logging.info("\n--- Graph connectivity ---")
    undirected = graph.to_undirected()
    components = list(nx.connected_components(undirected))
    logging.info(f"  Connected components: {len(components)}")
    if components:
        largest = max(components, key=len)
        logging.info(
            f"  Largest component: {len(largest)} nodes "
            f"({100 * len(largest) / graph.number_of_nodes():.1f}%)"
        )

    # Q7: Path example — can we reach from one plan to another via standards?
    logging.info("\n--- Path examples ---")
    plans = [
        nid
        for nid, d in graph.nodes(data=True)
        if d.get("node_type") == NodeType.PLAN.value
    ]
    if len(plans) >= 2:
        try:
            path = nx.shortest_path(undirected, plans[0], plans[1])
            logging.info(
                f"  Shortest path {plans[0]} → {plans[1]}: "
                f"{len(path)} hops"
            )
            for i, nid in enumerate(path):
                ndata = graph.nodes[nid]
                nt = ndata.get("node_type", "?")
                label = ndata.get("plan_id", "") or ndata.get("req_id", "") or ndata.get("feature_id", "") or nid
                logging.info(f"    [{i}] {nt}: {label}")
        except nx.NetworkXNoPath:
            logging.info(f"  No path between {plans[0]} and {plans[1]}")


if __name__ == "__main__":
    main()
