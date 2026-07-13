"""Extractor registry — maps file extensions to format-specific extractors."""

from __future__ import annotations

from pathlib import Path

from core.src.extraction.base import BaseExtractor
from core.src.extraction.docx_extractor import DOCXExtractor
from core.src.extraction.pdf_extractor import PDFExtractor
from core.src.extraction.release_key import release_key
from core.src.extraction.xlsx_extractor import XLSXExtractor
from core.src.models.document import DocumentIR


# Extractor instances, keyed by file extension
_EXTRACTORS: dict[str, BaseExtractor] = {
    ".pdf": PDFExtractor(),
    ".docx": DOCXExtractor(),
    ".xlsx": XLSXExtractor(),
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
    detect_text_tables: bool = False,
    header_footer_margin_mode: str = "blanket",
    layout_provider: str = "",
    provider_table_grid: bool = True,
    images_root: "Path | None" = None,
) -> DocumentIR:
    """Extract a document using the appropriate format extractor.

    `images_root` redirects extracted-image artifacts into the build output
    (the pipeline passes the cell's `out/extract/<mno>/<rel>/images`) — the
    input corpus may be a read-only mount. Unset = legacy next-to-source
    `extracted_images/` (ad-hoc CLI use only).
    """
    extractor = get_extractor(file_path)
    return extractor.extract(
        file_path, mno=mno, release=release, doc_type=doc_type,
        detect_text_tables=detect_text_tables,
        header_footer_margin_mode=header_footer_margin_mode,
        layout_provider=layout_provider,
        provider_table_grid=provider_table_grid,
        images_root=images_root,
    )


def infer_metadata_from_path(
    file_path: Path,
    root: Path | None = None,
) -> dict[str, str]:
    """Infer mno and release from folder structure (D-023, FR-30).

    Expected layout: <root>/<MNO>/<MMMYYYY>/filename.ext where `root` is the
    documents root (default `<env_dir>/input`; overridable via
    `requirements_dir`, e.g. mounted at /data/requirements in containers).

    When `root` is given, segments are taken RELATIVE TO IT — the reliable
    anchor. Without it (ad-hoc callers), fall back to locating a literal
    "input" segment, then to "last two dirs are MNO/release" (which cannot
    see per-plan subdirs — pass `root` whenever it is known).

    The release directory follows the MMMYYYY convention (D-DRAFT-6 — the
    `(MNO, release)` cell key, mirroring multi-mno-sira D-DRAFT-5). A
    non-conforming release is rejected **fail-loud here** (`EXT-E004`) via
    `release_key`, so a mis-named directory is caught at ingest rather than
    silently mis-ordered downstream.

    `doc_type` defaults to "requirement" — v1 has only requirements docs;
    FR-26 (test-case parser) is deferred.
    """
    parts = file_path.resolve().parts
    metadata = {"mno": "", "release": "", "doc_type": "requirement", "plan": ""}

    rel_parts: "tuple[str, ...] | None" = None
    if root is not None:
        try:
            rel_parts = file_path.resolve().relative_to(Path(root).resolve()).parts
        except ValueError:
            rel_parts = None  # not under root — fall back to legacy inference

    if rel_parts is not None:
        # <MNO>/<release>/[<plan>/]<file> relative to the documents root.
        if len(rel_parts) >= 3:
            metadata["mno"] = rel_parts[0].upper()
            metadata["release"] = rel_parts[1]
            if len(rel_parts) >= 4:
                metadata["plan"] = rel_parts[2]
    # Look for the "input" anchor; the two segments immediately after it are MNO/release.
    elif "input" in parts:
        idx = parts.index("input")
        if idx + 2 < len(parts):
            metadata["mno"] = parts[idx + 1].upper()
            metadata["release"] = parts[idx + 2]
            # A directory between <release> and the file is a per-plan dir
            # (input/<MNO>/<release>/<plan>/<file>) — capture it as the plan.
            # Flat layouts (file directly under <release>) leave plan empty.
            if idx + 4 < len(parts):
                metadata["plan"] = parts[idx + 3]
    elif len(parts) >= 3:
        # Fallback when no "input" anchor: assume last two dirs are MNO/release.
        metadata["release"] = parts[-2]
        metadata["mno"] = parts[-3].upper()

    # D-DRAFT-6: enforce the MMMYYYY release convention at the ingest
    # boundary. Validate only a parsed (non-empty) release — an empty
    # release means the path didn't carry the convention at all, a
    # separate layout problem the caller surfaces.
    if metadata["release"]:
        release_key(metadata["release"])  # raises ValueError (EXT-E004) on non-MMMYYYY

    return metadata
