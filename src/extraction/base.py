"""Base extractor interface for all format-specific extractors."""

from abc import ABC, abstractmethod
from pathlib import Path

from src.models.document import DocumentIR


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
    ) -> DocumentIR:
        """Extract content from a document file.

        Args:
            file_path: Path to the source document.
            mno: MNO identifier (e.g., "VZW"). Derived from folder structure.
            release: Release identifier (e.g., "2026_Feb"). Derived from folder structure.
            doc_type: Document type ("requirement" or "testcase"). Derived from folder structure.

        Returns:
            Normalized DocumentIR ready for the DocumentProfiler or structural parser.
        """
        ...
