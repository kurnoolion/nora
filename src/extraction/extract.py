"""CLI entry point for document content extraction.

Usage:
    # Extract a single document
    python -m src.extraction.extract data/raw/vzw/2026_feb/requirements/LTEDATARETRY.pdf

    # Extract all documents in a directory
    python -m src.extraction.extract data/raw/vzw/2026_feb/requirements/

    # Specify output directory
    python -m src.extraction.extract data/raw/vzw/2026_feb/requirements/ --output data/extracted
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.extraction.registry import extract_document, infer_metadata_from_path

SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}


def extract_file(file_path: Path, output_dir: Path) -> Path | None:
    """Extract a single file and save the result as JSON."""
    metadata = infer_metadata_from_path(file_path)
    logging.info(
        f"Metadata from path: mno={metadata['mno']}, "
        f"release={metadata['release']}, doc_type={metadata['doc_type']}"
    )

    try:
        ir = extract_document(
            file_path,
            mno=metadata["mno"],
            release=metadata["release"],
            doc_type=metadata["doc_type"],
        )
    except ValueError as e:
        logging.error(f"Skipping {file_path.name}: {e}")
        return None

    output_path = output_dir / f"{file_path.stem}_ir.json"
    ir.save_json(output_path)
    logging.info(f"Saved: {output_path} ({ir.block_count} blocks)")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Extract document content into normalized intermediate representation."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a document file or directory of documents.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/extracted"),
        help="Output directory for extracted JSON files. Default: data/extracted",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    input_path: Path = args.input
    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect files to process
    if input_path.is_file():
        files = [input_path]
    elif input_path.is_dir():
        files = sorted(
            f
            for f in input_path.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    else:
        logging.error(f"Path does not exist: {input_path}")
        sys.exit(1)

    if not files:
        logging.error(f"No supported files found in {input_path}")
        sys.exit(1)

    logging.info(f"Found {len(files)} file(s) to extract")

    start = time.time()
    results = []
    for file_path in files:
        t0 = time.time()
        output_path = extract_file(file_path, output_dir)
        elapsed = time.time() - t0
        if output_path:
            results.append((file_path.name, output_path, elapsed))
            logging.info(f"  {file_path.name}: {elapsed:.1f}s")

    total_elapsed = time.time() - start
    logging.info(f"\nExtraction complete: {len(results)}/{len(files)} files in {total_elapsed:.1f}s")
    for name, out, t in results:
        logging.info(f"  {name} -> {out}")


if __name__ == "__main__":
    main()
