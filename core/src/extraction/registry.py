"""Extractor registry — maps file extensions to format-specific extractors."""

from __future__ import annotations

from pathlib import Path

from core.src.extraction.base import BaseExtractor
from core.src.extraction.docx_extractor import DOCXExtractor
from core.src.extraction.pdf_extractor import PDFExtractor
from core.src.models.document import DocumentIR


# Extractor instances, keyed by file extension
_EXTRACTORS: dict[str, BaseExtractor] = {
    ".pdf": PDFExtractor(),
    ".docx": DOCXExtractor(),
}


def supported_extensions() -> set[str]:
    """Return the set of file extensions with registered extractors."""
    return set(_EXTRACTORS.keys())


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
    """Infer mno and release from folder structure (D-023, FR-30).

    Expected layout: <env_dir>/input/<MNO>/<release>/filename.ext
    e.g., /data/vzw-feb2026/input/VZW/Feb2026/LTEDATARETRY.pdf

    `doc_type` defaults to "requirement" — v1 has only requirements docs;
    FR-26 (test-case parser) is deferred.
    """
    parts = file_path.resolve().parts
    metadata = {"mno": "", "release": "", "doc_type": "requirement"}

    # Look for the "input" anchor; the two segments immediately after it are MNO/release.
    if "input" in parts:
        idx = parts.index("input")
        if idx + 2 < len(parts):
            metadata["mno"] = parts[idx + 1].upper()
            metadata["release"] = parts[idx + 2]
    elif len(parts) >= 3:
        # Fallback when no "input" anchor: assume last two dirs are MNO/release.
        metadata["release"] = parts[-2]
        metadata["mno"] = parts[-3].upper()

    return metadata
