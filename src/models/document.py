"""Normalized intermediate representation for extracted documents.

Matches the schema defined in TDD Section 5.1.7. All format-specific
extractors produce this common representation, consumed uniformly by
the DocumentProfiler and structural parsers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    EMBEDDED_OBJECT = "embedded_object"


@dataclass
class Position:
    """Location of a content block within the source document."""
    page: int
    index: int  # sequential index across the whole document
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1)


@dataclass
class FontInfo:
    """Font attributes for text-based content blocks.

    Critical for the DocumentProfiler's heading detection — it clusters
    blocks by font size/boldness to derive heading level rules.
    """
    size: float
    bold: bool = False
    italic: bool = False
    font_name: str = ""
    all_caps: bool = False
    color: int = 0  # RGB as integer


@dataclass
class ContentBlock:
    """A single content block in the normalized intermediate representation."""
    type: BlockType
    position: Position

    # Text content (for heading, paragraph)
    text: str = ""
    level: int | None = None  # heading level (from DOCX styles; None for PDF)
    font_info: FontInfo | None = None  # primary font of the block (PDF extraction)
    style: str = ""  # DOCX style name if available

    # Table content
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    # Image content
    image_path: str = ""
    surrounding_text: str = ""

    # Embedded object content
    object_type: str = ""  # "xlsx", "docx", "pdf", etc.
    extracted_path: str = ""
    extracted_content: dict[str, Any] = field(default_factory=dict)

    # Metadata hints for downstream processing
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentIR:
    """Normalized intermediate representation for a single document.

    This is the output of the extraction layer and input to both
    the DocumentProfiler and the structural parser.
    """
    source_file: str
    source_format: str  # "pdf", "doc", "docx", "xls", "xlsx"
    mno: str = ""
    release: str = ""
    doc_type: str = ""  # "requirement", "testcase"
    content_blocks: list[ContentBlock] = field(default_factory=list)
    extraction_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        if not self.content_blocks:
            return 0
        return max(b.position.page for b in self.content_blocks)

    @property
    def block_count(self) -> int:
        return len(self.content_blocks)

    def blocks_by_type(self, block_type: BlockType) -> list[ContentBlock]:
        return [b for b in self.content_blocks if b.type == block_type]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)

    def save_json(self, path: Path) -> None:
        """Save the IR to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_json(cls, path: Path) -> DocumentIR:
        """Load an IR from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        blocks = []
        for b in data.get("content_blocks", []):
            pos = Position(**b["position"])
            font = FontInfo(**b["font_info"]) if b.get("font_info") else None
            blocks.append(ContentBlock(
                type=BlockType(b["type"]),
                position=pos,
                text=b.get("text", ""),
                level=b.get("level"),
                font_info=font,
                style=b.get("style", ""),
                headers=b.get("headers", []),
                rows=b.get("rows", []),
                image_path=b.get("image_path", ""),
                surrounding_text=b.get("surrounding_text", ""),
                object_type=b.get("object_type", ""),
                extracted_path=b.get("extracted_path", ""),
                extracted_content=b.get("extracted_content", {}),
                metadata=b.get("metadata", {}),
            ))
        return cls(
            source_file=data["source_file"],
            source_format=data["source_format"],
            mno=data.get("mno", ""),
            release=data.get("release", ""),
            doc_type=data.get("doc_type", ""),
            content_blocks=blocks,
            extraction_metadata=data.get("extraction_metadata", {}),
        )
