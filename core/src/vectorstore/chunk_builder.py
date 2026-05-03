"""Chunk builder for contextualized requirement chunks (TDD 5.9).

Converts each requirement from parsed trees into a text chunk with
metadata suitable for vector store ingestion.

Design decisions:
- Each requirement node = one chunk (no arbitrary chunking)
- Chunks are contextualized with structural context (MNO, hierarchy path)
- Tables serialized as Markdown within chunk text
- Metadata enables filtering by MNO, release, plan, feature
- FR-35 [D-032]: per-document `definitions_map` is threaded in from the
  RequirementTree and inline-expanded into chunk text on first occurrence
  of each known term, before embedding. Chunks belonging to the
  definitions section itself are excluded from expansion to avoid
  double-anchoring.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.src.vectorstore.config import VectorStoreConfig

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

        # FR-35 [D-032]: per-document definitions map and the section
        # number of the definitions section (used to skip self-expansion
        # within the section's own chunks and its descendants).
        definitions_map = tree.get("definitions_map", {}) or {}
        defs_section_num = tree.get("definitions_section_number", "") or ""
        defs_pattern = self._compile_definitions_regex(definitions_map)

        # Build a `req_id → title` lookup once per tree so
        # `_build_chunk_text` can resolve children's titles for the
        # `[Subsections: ...]` augmentation. children references are
        # req_id strings; the tree's `requirements` list is flat with
        # paragraph + table-anchored reqs both present.
        id_to_title: dict[str, str] = {}
        for r in tree.get("requirements", []):
            rid = r.get("req_id", "")
            if rid:
                id_to_title[rid] = (r.get("title", "") or "").strip()

        chunks = []
        for req in tree.get("requirements", []):
            req_id = req.get("req_id", "")
            if not req_id:
                continue

            text = self._build_chunk_text(
                req, mno, release, plan_name, version, id_to_title,
            )

            # Skip chunks with no meaningful content
            if not text.strip():
                continue

            # FR-35 [D-032]: inline-expand definitions on first occurrence
            # of each known term, except for chunks within the definitions
            # section itself (avoid double-anchoring).
            if defs_pattern is not None and not self._belongs_to_definitions(
                req, defs_section_num
            ):
                text = self._expand_definitions(text, defs_pattern, definitions_map)

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

        # One short, retrieval-friendly chunk per glossary entry
        # (D-043). The whole acronym table also lives inside the
        # definitions-section req chunk, but that chunk is dominated
        # by the other 18 entries — short queries like "What is X?"
        # rank it low. A dedicated chunk with the acronym in the
        # first ~80 chars wins on both BM25 (high TF-IDF) and dense
        # similarity (concise definition).
        for entry in self._build_glossary_chunks(
            definitions_map=definitions_map,
            mno=mno,
            release=release,
            plan_id=plan_id,
            plan_name=plan_name,
            defs_section_num=defs_section_num,
            feature_ids=feature_ids,
        ):
            chunks.append(entry)

        return chunks

    @staticmethod
    def _build_glossary_chunks(
        definitions_map: dict[str, str],
        mno: str,
        release: str,
        plan_id: str,
        plan_name: str,
        defs_section_num: str,
        feature_ids: list[str],
    ) -> list[Chunk]:
        """Build one Chunk per (acronym, expansion) pair.

        These are tiny — a header, the acronym, the expansion — so
        BM25 weights the acronym heavily and dense embedding
        captures the natural-language definition. Used for queries
        like "What is SDM?" where the answer is the expansion text
        itself, not the requirement-shaped chunk that contains it.

        chunk_id format: `glossary:<plan_id>:<acronym>`. We slug
        the acronym for filesystem/store safety.
        """
        out: list[Chunk] = []
        for acronym, expansion in (definitions_map or {}).items():
            term = (acronym or "").strip()
            exp = (expansion or "").strip()
            if not term or not exp:
                continue
            # Header lines mirror the per-requirement chunk format so
            # BM25 / dense models see consistent prefixes across the
            # corpus.
            text = (
                f"[MNO: {mno} | Release: {release} | Plan: {plan_name or plan_id}]\n"
                f"[Glossary entry — {plan_id}]\n"
                f"[Acronym: {term}]\n\n"
                f"{term}: {exp}"
            )
            slug = re.sub(r"[^A-Za-z0-9_-]+", "_", term).strip("_") or "term"
            chunk_id = f"glossary:{plan_id}:{slug}"
            out.append(Chunk(
                chunk_id=chunk_id,
                text=text,
                metadata={
                    "mno": mno,
                    "release": release,
                    "doc_type": "glossary_entry",
                    "plan_id": plan_id,
                    "req_id": "",
                    "section_number": defs_section_num,
                    "zone_type": "",
                    "feature_ids": feature_ids,
                    "acronym": term,
                    "expansion": exp,
                },
            ))
        return out

    @staticmethod
    def _compile_definitions_regex(definitions_map: dict[str, str]):
        """Compile a single alternation regex matching every term as a
        whole-word match. Returns None when the map is empty."""
        if not definitions_map:
            return None
        # Sort by length descending so longer terms match first (avoids
        # `RAT` consuming the start of `RATIO` or similar, and lets
        # `IMS REGISTRATION` match before bare `IMS` if both are defined).
        terms_sorted = sorted(definitions_map.keys(), key=len, reverse=True)
        # Escape each term to be regex-safe; word boundaries on both sides.
        alternation = "|".join(re.escape(t) for t in terms_sorted)
        return re.compile(rf"\b({alternation})\b")

    @staticmethod
    def _belongs_to_definitions(req: dict, defs_section_num: str) -> bool:
        """True when the requirement is the definitions section or a
        descendant of it (paragraph- or table-anchored)."""
        if not defs_section_num:
            return False
        sec_num = req.get("section_number", "")
        parent = req.get("parent_section", "")
        # Paragraph-anchored: section_number == defs OR descendant by prefix
        if sec_num:
            if sec_num == defs_section_num:
                return True
            if sec_num.startswith(defs_section_num + "."):
                return True
        # Table-anchored: parent_section identifies the owning paragraph
        if parent:
            if parent == defs_section_num:
                return True
            if parent.startswith(defs_section_num + "."):
                return True
        return False

    @staticmethod
    def _expand_definitions(
        text: str, pattern: "re.Pattern[str]", definitions_map: dict[str, str]
    ) -> str:
        """Inline-expand the first occurrence of each known term in `text`.

        Each term is expanded once per chunk: `ETWS` →
        `ETWS (Earthquake and Tsunami Warning System)`. Subsequent
        occurrences within the same chunk are left untouched (avoids
        bloat). The expansion is idempotent: re-running on already-expanded
        text is a no-op because the inserted parenthetical breaks the
        word boundary on the next match.
        """
        seen: set[str] = set()

        def repl(m: "re.Match[str]") -> str:
            term = m.group(1)
            if term in seen:
                return term
            seen.add(term)
            expansion = definitions_map.get(term, "")
            if not expansion:
                return term
            return f"{term} ({expansion})"

        return pattern.sub(repl, text)

    def _build_chunk_text(
        self,
        req: dict,
        mno: str,
        release: str,
        plan_name: str,
        version: str,
        id_to_title: dict[str, str] | None = None,
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

        # Subsection titles — lifts thin parent/overview chunks for
        # breadth queries. Gated by body-thinness: only parents with
        # body text below `children_titles_body_threshold` get
        # augmented. Augmenting substantial-body parents displaces
        # their children from cross-doc top-k (parents already rank
        # well; the breadth query actually wants the leaves).
        cap = max(0, self.config.max_children_titles)
        body_thinness_ok = (
            len((body or "").strip())
            < self.config.children_titles_body_threshold
        )
        if (
            self.config.include_children_titles
            and id_to_title
            and cap > 0
            and body_thinness_ok
        ):
            children = req.get("children", []) or []
            child_titles: list[str] = []
            for cid in children:
                t = id_to_title.get(cid, "")
                if t:
                    child_titles.append(t)
            if child_titles:
                if len(child_titles) > cap:
                    extra = len(child_titles) - cap
                    visible = child_titles[:cap]
                    visible.append(f"(+{extra} more)")
                    child_titles = visible
                parts.append(f"[Subsections: {'; '.join(child_titles)}]")

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
