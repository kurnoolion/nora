"""CLI entry point for the generic structural parser.

Usage:
    # Parse with a document profile (profile-driven mode)
    python -m core.src.parser.parse_cli \
        --profile profiles/vzw_oa_profile.json \
        --doc data/extracted/LTEDATARETRY_ir.json \
        --output data/parsed/LTEDATARETRY_tree.json

    # Parse without a profile (style-driven mode — DOCX only)
    python -m core.src.parser.parse_cli \
        --no-profile \
        --doc data/extracted/LTEDATARETRY_ir.json \
        --output data/parsed/LTEDATARETRY_tree.json

    # Batch parse a directory (profile-driven)
    python -m core.src.parser.parse_cli \
        --profile profiles/vzw_oa_profile.json \
        --docs-dir data/extracted \
        --output-dir data/parsed
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from core.src.models.document import DocumentIR
from core.src.profiler.profile_schema import DocumentProfile
from core.src.parser.structural_parser import GenericStructuralParser
from core.src.parser.style_parser import StyleDrivenParser


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Parse extracted documents into requirement trees. "
            "Supply --profile for profile-driven mode or --no-profile for "
            "style-driven mode (DOCX-origin IRs with Word heading styles)."
        )
    )

    profile_group = parser.add_mutually_exclusive_group(required=True)
    profile_group.add_argument(
        "--profile", type=Path,
        help="Path to document profile JSON (profile-driven mode)",
    )
    profile_group.add_argument(
        "--no-profile", action="store_true",
        help=(
            "Style-driven mode: derive heading hierarchy from Word heading "
            "levels embedded in the IR (block.level). No profile.json needed. "
            "DOCX-origin IRs only."
        ),
    )

    doc_group = parser.add_mutually_exclusive_group(required=True)
    doc_group.add_argument("--doc", type=Path, help="Path to a single extracted IR JSON")
    doc_group.add_argument("--docs-dir", type=Path, help="Directory of extracted IR JSONs")

    parser.add_argument("--output", type=Path, help="Output path (for single doc)")
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/parsed"),
        help="Output directory (for batch). Default: data/parsed",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.no_profile:
        structural_parser = StyleDrivenParser()
    else:
        profile = DocumentProfile.load_json(args.profile)
        structural_parser = GenericStructuralParser(profile)

    if args.doc:
        files = [args.doc]
        output_dir = args.output.parent if args.output else args.output_dir
    else:
        files = sorted(args.docs_dir.glob("*_ir.json"))
        output_dir = args.output_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    start = time.time()
    for f in files:
        t0 = time.time()
        doc = DocumentIR.load_json(f)
        tree = structural_parser.parse(doc)

        out_name = f.stem.replace("_ir", "_tree") + ".json"
        out_path = args.output if (args.output and len(files) == 1) else output_dir / out_name
        tree.save_json(out_path)

        elapsed = time.time() - t0
        logging.info(
            f"  {doc.source_file}: {len(tree.requirements)} requirements, "
            f"{elapsed:.1f}s -> {out_path}"
        )

    total = time.time() - start
    logging.info(f"\nParsed {len(files)} file(s) in {total:.1f}s")


if __name__ == "__main__":
    main()
