"""CLI entry point for feature taxonomy extraction and consolidation.

Usage:
    # Extract features from all parsed trees and build taxonomy
    python -m src.taxonomy.taxonomy_cli \
        --trees-dir data/parsed \
        --output-dir data/taxonomy

    # Extract from specific trees
    python -m src.taxonomy.taxonomy_cli \
        --trees data/parsed/LTEDATARETRY_tree.json data/parsed/LTESMS_tree.json \
        --output-dir data/taxonomy

    # Use verbose mode for debug-level logging
    python -m src.taxonomy.taxonomy_cli --trees-dir data/parsed -v

Output files:
    data/taxonomy/{plan_id}_features.json  — per-document feature extraction
    data/taxonomy/taxonomy.json            — unified taxonomy across all documents
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.llm.mock_provider import MockLLMProvider
from src.parser.structural_parser import RequirementTree
from src.taxonomy.consolidator import TaxonomyConsolidator
from src.taxonomy.extractor import FeatureExtractor


def main():
    parser = argparse.ArgumentParser(
        description="Extract telecom feature taxonomy from parsed requirement trees."
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
        "--output-dir", type=Path, default=Path("data/taxonomy"),
        help="Output directory for taxonomy JSON files. Default: data/taxonomy",
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
        logging.info(
            f"  {f.name}: {len(tree.requirements)} requirements "
            f"({time.time() - t0:.1f}s)"
        )
        trees.append(tree)

    # Step 1: Per-document feature extraction
    logging.info("\n--- Step 1: Per-document feature extraction ---")
    llm = MockLLMProvider()
    extractor = FeatureExtractor(llm)

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    doc_features_list = []
    for tree in trees:
        t0 = time.time()
        doc_features = extractor.extract(tree)
        elapsed = time.time() - t0

        # Save per-document features
        out_path = output_dir / f"{tree.plan_id}_features.json"
        doc_features.save_json(out_path)
        logging.info(
            f"  Saved {out_path.name} "
            f"({len(doc_features.primary_features)} primary, "
            f"{len(doc_features.referenced_features)} referenced, "
            f"{elapsed:.1f}s)"
        )
        doc_features_list.append(doc_features)

    # Step 2: Consolidation into unified taxonomy
    logging.info("\n--- Step 2: Taxonomy consolidation ---")
    t0 = time.time()
    consolidator = TaxonomyConsolidator()
    taxonomy = consolidator.consolidate(doc_features_list)
    elapsed = time.time() - t0

    taxonomy_path = output_dir / "taxonomy.json"
    taxonomy.save_json(taxonomy_path)
    logging.info(f"Saved unified taxonomy to {taxonomy_path} ({elapsed:.1f}s)")

    # Print summary table
    logging.info(f"\n{'='*70}")
    logging.info(f"Feature Taxonomy Summary")
    logging.info(f"{'='*70}")
    logging.info(f"MNO: {taxonomy.mno}")
    logging.info(f"Release: {taxonomy.release}")
    logging.info(f"Source documents: {len(taxonomy.source_documents)}")
    logging.info(f"Total features: {len(taxonomy.features)}")
    logging.info(f"LLM calls: {llm.call_count} (MockLLMProvider)")
    logging.info(f"")
    logging.info(f"{'Feature ID':<25s}  {'Name':<35s}  {'Primary In':>10s}  {'Ref In':>8s}")
    logging.info(f"{'-'*25}  {'-'*35}  {'-'*10}  {'-'*8}")
    for f in taxonomy.features:
        logging.info(
            f"{f.feature_id:<25s}  {f.name:<35s}  "
            f"{len(f.is_primary_in):>10d}  {len(f.is_referenced_in):>8d}"
        )

    logging.info(f"\nOutput files:")
    logging.info(f"  Per-document: {output_dir}/<plan_id>_features.json")
    logging.info(f"  Unified:      {taxonomy_path}")


if __name__ == "__main__":
    main()
