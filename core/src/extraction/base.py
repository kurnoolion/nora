"""Base extractor interface for all format-specific extractors."""

from abc import ABC, abstractmethod
from pathlib import Path

from core.src.models.document import DocumentIR


class BaseExtractor(ABC):
    """Abstract base class for document content extractors.

    Each format (PDF, DOCX, etc.) implements this interface.
    All extractors produce the same normalized DocumentIR output.
    """

    @abstractmethod
    def extract(
        self,
        file_path: Path,
        mno: str = "",
        release: str = "",
        doc_type: str = "",
        detect_text_tables: bool = False,
        header_footer_margin_mode: str = "blanket",
        layout_provider: str = "",
        provider_table_grid: bool = True,
    ) -> DocumentIR:
        """Extract content from a document file.

        Args:
            file_path: Path to the source document.
            mno: MNO identifier (e.g., "VZW"). Derived from folder structure.
            release: Release identifier (e.g., "2026_Feb"). Derived from folder structure.
            doc_type: Document type ("requirement" or "testcase"). Derived from folder structure.
            detect_text_tables: per-corpus PDF hint — enable text-alignment
                (borderless) table detection. PDF-only; other extractors ignore it.
            header_footer_margin_mode: per-corpus PDF hint — "blanket" drops all
                page-margin text; "pattern_only" keeps it unless it matches a
                header/footer pattern. PDF-only; other extractors ignore it.
            layout_provider: per-corpus PDF hint — name of a LayoutProvider (e.g.
                "docling") to source tables + figures from; "" uses the built-in
                geometric path. PDF-only; other extractors ignore it.
            provider_table_grid: when False, layout-provider tables carry HTML only
                (skip the flat headers/rows the anchoring path would use). PDF-only;
                other extractors ignore it.

        Returns:
            Normalized DocumentIR ready for the DocumentProfiler or structural parser.
        """
        ...
