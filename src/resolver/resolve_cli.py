"""CLI entry point for cross-reference resolution.

Usage:
    # Resolve all parsed trees in a directory
    python -m src.resolver.resolve_cli \
        --trees-dir data/parsed \
        --output-dir data/resolved

    # Resolve specific trees
    python -m src.resolver.resolve_cli \
        --trees data/parsed/LTEDATARETRY_tree.json data/parsed/LTEB13NAC_tree.json \
        --output-dir data/resolved
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.parser.structural_parser import RequirementTree
from src.resolver.resolver import CrossReferenceResolver


def main():
    parser = argparse.ArgumentParser(
        description="Resolve cross-references across parsed requirement trees."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--trees-dir", type=Path,
        help="Directory containing *_tree.json files",
    )
    group.add_argument(
        "--trees", nargs="+", type=Path,
        help="Paths to specific tree JSON files",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/resolved"),
        help="Output directory for manifest JSON files. Default: data/resolved",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Load trees
    if args.trees_dir:
        tree_files = sorted(args.trees_dir.glob("*_tree.json"))
    else:
        tree_files = args.trees

    if not tree_files:
        logging.error("No tree files found")
        return

    logging.info(f"Loading {len(tree_files)} tree(s)")
    trees = []
    for f in tree_files:
        t0 = time.time()
        tree = RequirementTree.load_json(f)
        logging.info(f"  {f.name}: {len(tree.requirements)} requirements ({time.time() - t0:.1f}s)")
        trees.append(tree)

    # Resolve
    t0 = time.time()
    resolver = CrossReferenceResolver(trees)
    manifests = resolver.resolve_all()
    elapsed = time.time() - t0

    # Save
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    for manifest in manifests:
        out_path = output_dir / f"{manifest.plan_id}_xrefs.json"
        manifest.save_json(out_path)

    # Print summary
    logging.info(f"\nResolution complete in {elapsed:.1f}s")
    logging.info(f"{'Plan':<20s}  {'Internal':>12s}  {'Cross-plan':>12s}  {'Standards':>12s}")
    logging.info("-" * 60)
    for m in manifests:
        s = m.summary
        int_str = f"{s.resolved_internal}/{s.total_internal}"
        xp_str = f"{s.resolved_cross_plan}/{s.total_cross_plan}"
        std_str = f"{s.resolved_standards}/{s.total_standards}"
        logging.info(f"{m.plan_id:<20s}  {int_str:>12s}  {xp_str:>12s}  {std_str:>12s}")

    # Overall totals
    tot_int = sum(m.summary.total_internal for m in manifests)
    res_int = sum(m.summary.resolved_internal for m in manifests)
    brk_int = sum(m.summary.broken_internal for m in manifests)
    tot_xp = sum(m.summary.total_cross_plan for m in manifests)
    res_xp = sum(m.summary.resolved_cross_plan for m in manifests)
    tot_std = sum(m.summary.total_standards for m in manifests)
    res_std = sum(m.summary.resolved_standards for m in manifests)

    logging.info("-" * 60)
    logging.info(
        f"Total: internal={res_int}/{tot_int} (broken={brk_int}), "
        f"cross-plan={res_xp}/{tot_xp}, "
        f"standards={res_std}/{tot_std}"
    )
    logging.info(f"\nManifests saved to: {output_dir}/")


if __name__ == "__main__":
    main()
