"""LayoutProvider — pluggable source of table + figure structure for extraction.

A layout provider (e.g. Docling) parses a PDF and returns its **tables and
figures**; the extractor reconciles their coordinates into pymupdf's frame,
turns them into ``TABLE`` / ``IMAGE`` ``ContentBlock``s, and merges them with the
pymupdf **text** blocks. The pymupdf text layer + the profile-driven structural
parser still own requirement text — the provider only supplies structure.

Selection is per-corpus via ``DocumentProfile.layout_provider`` (opt-in, D-003).
This mirrors the project's other protocol abstractions (``LLMProvider``,
``EmbeddingProvider``, ``VectorStoreProvider``). Providers are expected to be
optional heavy dependencies: ``available()`` gates a missing dep / models so a
profile selecting an uninstalled provider fails loud rather than silently.

Bboxes are returned in the provider's own top-left-origin **points** frame plus
`page_sizes`, so the extractor can detect and correct a rotation / CropBox
mismatch against pymupdf's page geometry before fusing by position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# bbox is (x0, y0, x1, y1), top-left origin, PDF points, provider frame.
BBox = tuple[float, float, float, float]


@dataclass
class LayoutTable:
    page: int                                  # 1-based
    bbox: BBox | None = None
    html: str = ""                             # lossless (keeps merged cells)
    headers: list[str] = field(default_factory=list)   # best-effort flat grid
    rows: list[list[str]] = field(default_factory=list)  # for the anchoring path


@dataclass
class LayoutFigure:
    page: int
    bbox: BBox | None = None
    image_path: str = ""                       # saved crop, if the provider emits one
    caption: str = ""


@dataclass
class LayoutStructures:
    """A provider's tables + figures for one document."""
    tables: list[LayoutTable] = field(default_factory=list)
    figures: list[LayoutFigure] = field(default_factory=list)
    # page_no -> (width, height) in the provider's points frame.
    page_sizes: dict[int, tuple[float, float]] = field(default_factory=dict)


@runtime_checkable
class LayoutProvider(Protocol):
    name: str

    def available(self) -> tuple[bool, str]:
        """(is_usable, reason). False when the dependency or its models are
        missing — the caller surfaces `reason` and fails loud rather than
        silently degrading."""
        ...

    def extract_layout(
        self, pdf_path: Path, image_dir: Path | None = None,
        want_table_grid: bool = True,
    ) -> LayoutStructures:
        """Return tables + figures for the PDF. When `image_dir` is given, figure
        crops are saved there and referenced via `LayoutFigure.image_path`. When
        `want_table_grid` is False, skip the best-effort flat `headers`/`rows`
        (HTML is the lossless form; the grid only feeds the table-anchored req-id
        path). Must not raise for recoverable errors — return empty structures so
        extraction falls back to the geometric path rather than aborting."""
        ...


def get_layout_provider(name: str) -> LayoutProvider | None:
    """Resolve a provider by profile name, or None if unknown. Providers are
    lazy-imported so their heavy optional deps aren't loaded until selected."""
    if name == "docling":
        from core.src.extraction.docling_provider import DoclingProvider
        return DoclingProvider()
    return None
