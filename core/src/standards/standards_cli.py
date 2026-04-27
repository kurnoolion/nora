"""CLI entry point for standards ingestion pipeline.

Usage:
    # Full pipeline: collect refs, download specs, parse, extract sections
    python -m src.standards.standards_cli \
        --manifests-dir data/resolved \
        --trees-dir data/parsed \
        --output-dir data/standards

    # Collect references only (no download)
    python -m src.standards.standards_cli \
        --manifests-dir data/resolved \
        --trees-dir data/parsed \
        --output-dir data/standards \
        --collect-only

    # Limit download to specific specs
    python -m src.standards.standards_cli \
        --manifests-dir data/resolved \
        --trees-dir data/parsed \
        --output-dir data/standards \
        --specs 24.301 36.331

    # Skip download (use already-cached specs)
    python -m src.standards.standards_cli \
        --manifests-dir data/resolved \
        --trees-dir data/parsed \
        --output-dir data/standards \
        --no-download

Output files:
    data/standards/reference_index.json                — aggregated reference index
    data/standards/TS_{spec}/Rel-{N}/{file}.docx       — downloaded spec documents
    data/standards/TS_{spec}/Rel-{N}/spec_parsed.json   — parsed section tree
    data/standards/TS_{spec}/Rel-{N}/sections.json      — extracted referenced sections
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from src.standards.reference_collector import StandardsReferenceCollector
from src.standards.schema import AggregatedSpecRef
from src.standards.section_extractor import SectionExtractor
from src.standards.spec_downloader import SpecDownloader
from src.standards.spec_parser import SpecParser


def main():
    parser = argparse.ArgumentParser(
        description="Ingest 3GPP standards specs referenced by requirement documents."
    )
    parser.add_argument(
        "--manifests-dir", type=Path, default=Path("data/resolved"),
        help="Directory containing *_xrefs.json manifests. Default: data/resolved",
    )
    parser.add_argument(
        "--trees-dir", type=Path, default=Path("data/parsed"),
        help="Directory containing *_tree.json parsed trees. Default: data/parsed",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/standards"),
        help="Output/cache directory for standards. Default: data/standards",
    )
    parser.add_argument(
        "--specs", nargs="*", type=str,
        help="Only process these spec numbers (e.g., 24.301 36.331)",
    )
    parser.add_argument(
        "--collect-only", action="store_true",
        help="Only collect references, do not download or parse specs",
    )
    parser.add_argument(
        "--no-download", action="store_true",
        help="Skip download, only parse/extract from cached specs",
    )
    parser.add_argument(
        "--max-specs", type=int, default=0,
        help="Limit number of specs to process (0 = all). Useful for testing.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    t0 = time.time()

    # Step 1: Collect references
    logging.info("=" * 60)
    logging.info("Step 1: Collecting standards references")
    logging.info("=" * 60)
    collector = StandardsReferenceCollector()
    index = collector.collect(
        manifest_dir=args.manifests_dir,
        trees_dir=args.trees_dir,
    )

    # Save reference index
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "reference_index.json"
    index.save_json(index_path)
    logging.info(f"Saved reference index to {index_path}")

    # Summary table
    _print_reference_summary(index)

    if args.collect_only:
        logging.info(f"\nDone (collect-only mode). Elapsed: {time.time() - t0:.1f}s")
        return

    # Filter specs if requested
    specs_to_process = index.specs
    if args.specs:
        spec_set = set(args.specs)
        specs_to_process = [s for s in specs_to_process if s.spec in spec_set]
        logging.info(f"\nFiltered to {len(specs_to_process)} specs: {args.specs}")

    # Only process specs with a known release
    specs_to_process = [s for s in specs_to_process if s.release_num > 0]
    if args.max_specs > 0:
        specs_to_process = specs_to_process[:args.max_specs]

    logging.info(
        f"\nProcessing {len(specs_to_process)} spec-release pairs "
        f"(skipping {len(index.specs) - len(specs_to_process)} without release)"
    )

    # Step 2: Download, parse, extract
    logging.info("\n" + "=" * 60)
    logging.info("Step 2: Download, parse, and extract")
    logging.info("=" * 60)

    downloader = SpecDownloader(cache_dir=output_dir)
    spec_parser = SpecParser()
    extractor = SectionExtractor()

    stats = {"downloaded": 0, "parsed": 0, "extracted": 0, "failed": 0}

    for spec_ref in specs_to_process:
        _process_spec(
            spec_ref, downloader, spec_parser, extractor,
            output_dir, args.no_download, stats,
        )

    # Final summary
    elapsed = time.time() - t0
    logging.info(f"\n{'=' * 60}")
    logging.info(f"Standards Ingestion Complete ({elapsed:.1f}s)")
    logging.info(f"{'=' * 60}")
    logging.info(
        f"Downloaded: {stats['downloaded']}, "
        f"Parsed: {stats['parsed']}, "
        f"Extracted: {stats['extracted']}, "
        f"Failed: {stats['failed']}"
    )
    logging.info(f"Output: {output_dir}/")


def _process_spec(
    spec_ref: AggregatedSpecRef,
    downloader: SpecDownloader,
    spec_parser: SpecParser,
    extractor: SectionExtractor,
    output_dir: Path,
    no_download: bool,
    stats: dict,
) -> None:
    """Download, parse, and extract sections for one spec-release pair."""
    label = f"TS {spec_ref.spec} Rel-{spec_ref.release_num}"

    # Download
    if no_download:
        doc_path = downloader._find_cached(spec_ref.spec, spec_ref.release_num)
    else:
        doc_path = downloader.download(spec_ref.spec, spec_ref.release_num)
        if doc_path:
            stats["downloaded"] += 1

    if not doc_path:
        logging.warning(f"  {label}: no document available — skipping")
        stats["failed"] += 1
        return

    # Parse
    spec_dir = output_dir / f"TS_{spec_ref.spec}" / f"Rel-{spec_ref.release_num}"
    parsed_path = spec_dir / "spec_parsed.json"

    try:
        spec_doc = spec_parser.parse(doc_path)
        spec_doc.save_json(parsed_path)
        stats["parsed"] += 1
    except Exception as e:
        logging.warning(f"  {label}: parse failed — {e}")
        stats["failed"] += 1
        return

    # Extract referenced sections
    sections_path = spec_dir / "sections.json"
    result = extractor.extract(
        spec_doc,
        spec_ref.sections,
        source_plans=spec_ref.source_plans,
    )
    result.save_json(sections_path)
    stats["extracted"] += 1


def _print_reference_summary(index):
    """Print a summary table of collected references."""
    logging.info(
        f"\n{'Spec':<12s}  {'Release':>10s}  {'Refs':>5s}  "
        f"{'Sections':>8s}  {'Plans'}"
    )
    logging.info("-" * 70)

    for s in sorted(index.specs, key=lambda x: (-x.ref_count, x.spec)):
        rel = s.release if s.release else "(none)"
        plans = ", ".join(s.source_plans)
        logging.info(
            f"{s.spec:<12s}  {rel:>10s}  {s.ref_count:>5d}  "
            f"{len(s.sections):>8d}  {plans}"
        )


if __name__ == "__main__":
    main()
