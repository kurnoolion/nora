"""Chunk builder for contextualized requirement chunks (TDD 5.9).

Converts each requirement from parsed trees into a text chunk with
metadata suitable for vector store ingestion.

Design decisions:
- Each requirement node = one chunk (no arbitrary chunking)
- Chunks are contextualized with structural context (MNO, hierarchy path)
- Tables serialized as Markdown within chunk text
- Metadata enables filtering by MNO, release, plan, feature
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.vectorstore.config import VectorStoreConfig

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single vector store chunk."""
    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ChunkBuilder:
    """Builds contextualized chunks from parsed requirement trees.

    Each requirement becomes one chunk with:
    - Contextualized text (header + path + req ID + body + tables + images)
    - Metadata dict for filtering (mno, release, plan_id, req_id, feature_ids, etc.)
    """

    def __init__(self, config: VectorStoreConfig) -> None:
        self.config = config

    def build_chunks(
        self,
        trees: list[dict],
        taxonomy: dict | None = None,
    ) -> list[Chunk]:
        """Build chunks from all parsed trees.

        Args:
            trees: List of parsed requirement tree dicts.
            taxonomy: Unified taxonomy dict (optional, for feature_ids metadata).

        Returns:
            List of Chunk objects ready for embedding.
        """
        # Pre-compute plan_id -> feature_ids mapping from taxonomy
        plan_features = self._build_plan_feature_map(taxonomy)

        chunks = []
        for tree in trees:
            tree_chunks = self._build_tree_chunks(tree, plan_features)
            chunks.extend(tree_chunks)

        logger.info(f"Built {len(chunks)} chunks from {len(trees)} trees")
        return chunks

    def _build_tree_chunks(
        self,
        tree: dict,
        plan_features: dict[str, list[str]],
    ) -> list[Chunk]:
        """Build chunks for all requirements in one tree."""
        mno = tree.get("mno", "")
        release = tree.get("release", "")
        plan_id = tree.get("plan_id", "")
        plan_name = tree.get("plan_name", "")
        version = tree.get("version", "")

        feature_ids = plan_features.get(plan_id, [])

        chunks = []
        for req in tree.get("requirements", []):
            req_id = req.get("req_id", "")
            if not req_id:
                continue

            text = self._build_chunk_text(
                req, mno, release, plan_name, version,
            )

            # Skip chunks with no meaningful content
            if not text.strip():
                continue

            metadata = {
                "mno": mno,
                "release": release,
                "doc_type": "requirement",
                "plan_id": plan_id,
                "req_id": req_id,
                "section_number": req.get("section_number", ""),
                "zone_type": req.get("zone_type", ""),
                "feature_ids": feature_ids,
            }

            chunk_id = f"req:{req_id}"
            chunks.append(Chunk(chunk_id=chunk_id, text=text, metadata=metadata))

        return chunks

    def _build_chunk_text(
        self,
        req: dict,
        mno: str,
        release: str,
        plan_name: str,
        version: str,
    ) -> str:
        """Build the contextualized text for a single requirement.

        Format (following TDD 5.9):
            [MNO: VZW | Release: Feb 2026 | Plan: LTE_Data_Retry | Version: 39]
            [Path: SCENARIOS > EMM SPECIFIC PROCEDURES > ATTACH REQUEST]
            [Req ID: VZ_REQ_LTEDATARETRY_7748]

            <requirement text>

            [Table: ...]
            | Col1 | Col2 |
            | ...  | ...  |

            [Image: ...]
            <caption>
        """
        parts = []

        # MNO / release / plan header
        if self.config.include_mno_header:
            header_parts = []
            if mno:
                header_parts.append(f"MNO: {mno}")
            if release:
                header_parts.append(f"Release: {release}")
            if plan_name:
                header_parts.append(f"Plan: {plan_name}")
            if version:
                header_parts.append(f"Version: {version}")
            if header_parts:
                parts.append(f"[{' | '.join(header_parts)}]")

        # Hierarchy path
        if self.config.include_hierarchy_path:
            hierarchy = req.get("hierarchy_path", [])
            if hierarchy:
                parts.append(f"[Path: {' > '.join(hierarchy)}]")

        # Requirement ID
        if self.config.include_req_id:
            req_id = req.get("req_id", "")
            if req_id:
                parts.append(f"[Req ID: {req_id}]")

        # Section title (always included — it's the heading)
        title = req.get("title", "")
        if title:
            parts.append(f"\n{title}")

        # Body text
        body = req.get("text", "")
        if body:
            parts.append(body)

        # Tables as Markdown
        if self.config.include_tables:
            for table in req.get("tables", []):
                table_md = self._table_to_markdown(table)
                if table_md:
                    parts.append(table_md)

        # Image context
        if self.config.include_image_context:
            for image in req.get("images", []):
                caption = image.get("surrounding_text", "")
                if caption:
                    parts.append(f"[Image: {caption}]")

        return "\n".join(parts)

    @staticmethod
    def _table_to_markdown(table: dict) -> str:
        """Convert a table dict to Markdown table format.

        Tables from the parser have 'headers' and 'rows'.
        Single-column tables with just req IDs (formatting artifacts)
        are included but compact.
        """
        headers = table.get("headers", [])
        rows = table.get("rows", [])

        if not rows:
            return ""

        # If headers are empty strings, try to use first row as header
        if headers and all(h == "" for h in headers):
            # Single empty-header table — likely a req ID artifact table
            # Still include it but compactly
            all_cells = [cell for row in rows for cell in row if cell.strip()]
            if all_cells:
                return "[Table: " + " | ".join(all_cells) + "]"
            return ""

        if not headers:
            # No headers at all — just format rows
            lines = []
            for row in rows:
                lines.append("| " + " | ".join(str(c) for c in row) + " |")
            return "\n".join(lines)

        # Normal table with headers
        lines = []
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            # Pad row to match header length
            padded = list(row) + [""] * (len(headers) - len(row))
            lines.append("| " + " | ".join(str(c) for c in padded[:len(headers)]) + " |")

        return "\n".join(lines)

    @staticmethod
    def _build_plan_feature_map(taxonomy: dict | None) -> dict[str, list[str]]:
        """Build a mapping from plan_id -> list of feature_ids.

        A plan maps to features where it appears as primary or referenced.
        """
        if not taxonomy:
            return {}

        plan_features: dict[str, list[str]] = {}
        for feat in taxonomy.get("features", []):
            fid = feat.get("feature_id", "")
            if not fid:
                continue
            for pid in feat.get("is_primary_in", []):
                plan_features.setdefault(pid, []).append(fid)
            for pid in feat.get("is_referenced_in", []):
                if fid not in plan_features.get(pid, []):
                    plan_features.setdefault(pid, []).append(fid)

        return plan_features
