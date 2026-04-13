"""Extractor registry — maps file extensions to format-specific extractors."""

from __future__ import annotations

from pathlib import Path

from src.extraction.base import BaseExtractor
from src.extraction.pdf_extractor import PDFExtractor
from src.models.document import DocumentIR


# Extractor instances, keyed by file extension
_EXTRACTORS: dict[str, BaseExtractor] = {
    ".pdf": PDFExtractor(),
}


def get_extractor(file_path: Path) -> BaseExtractor:
    """Get the appropriate extractor for a file based on its extension."""
    ext = file_path.suffix.lower()
    if ext not in _EXTRACTORS:
        supported = ", ".join(sorted(_EXTRACTORS.keys()))
        raise ValueError(
            f"No extractor for '{ext}' files. Supported: {supported}"
        )
    return _EXTRACTORS[ext]


def extract_document(
    file_path: Path,
    mno: str = "",
    release: str = "",
    doc_type: str = "",
) -> DocumentIR:
    """Extract a document using the appropriate format extractor."""
    extractor = get_extractor(file_path)
    return extractor.extract(file_path, mno=mno, release=release, doc_type=doc_type)


def infer_metadata_from_path(
    file_path: Path,
) -> dict[str, str]:
    """Infer mno, release, and doc_type from folder structure.

    Expected structure: .../<mno>/<release>/<doc_type>/filename.ext
    e.g., data/raw/vzw/2026_feb/requirements/LTEDATARETRY.pdf
    """
    parts = file_path.resolve().parts
    metadata = {"mno": "", "release": "", "doc_type": ""}

    # Walk backwards from the file to find the expected structure
    # filename -> doc_type_folder -> release_folder -> mno_folder
    if len(parts) >= 4:
        doc_type_folder = parts[-2].lower()
        if doc_type_folder in ("requirements", "testcases"):
            metadata["doc_type"] = (
                "requirement" if doc_type_folder == "requirements" else "testcase"
            )
            metadata["release"] = parts[-3]
            metadata["mno"] = parts[-4].upper()

    return metadata
