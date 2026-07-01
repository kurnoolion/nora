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

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.src.parser.structural_parser import build_context_string
from core.src.vectorstore.config import VectorStoreConfig

logger = logging.getLogger(__name__)

# chunk_role enum-equivalent. "primary" = participates in BM25 + dense
# retrieval. "marker" = stays in store for citation-by-req_id lookup,
# excluded from retrieval scoring. Plumbed via Chunk.metadata["chunk_role"];
# rag_retriever applies the where-filter, bm25_index.from_store applies
# the skip-during-build filter.
CHUNK_ROLE_PRIMARY = "primary"
CHUNK_ROLE_MARKER = "marker"


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
        # Compiled regex caches — recompiled per-builder, not per-chunk.
        self._normative_re = re.compile(
            config.normative_verb_pattern, re.IGNORECASE,
        )
        self._effectively_empty_res = [
            re.compile(p) for p in config.effectively_empty_patterns
        ]

    # ── Marker classification (heuristic for now — LLM-judged is a
    #    follow-up; see strand nora-retrieval-parent-displacement) ──

    def _is_effectively_empty(self, body: str) -> bool:
        """Body is empty, whitespace-only, or matches a known
        placeholder pattern (VOID / TBD / N/A / etc.)."""
        if not body or not body.strip():
            return True
        for pat in self._effectively_empty_res:
            if pat.match(body):
                return True
        return False

    def _classify_chunk_role(
        self, title: str, body: str,
    ) -> tuple[str, str]:
        """Heuristic classifier: return (role, reason).

        - body non-empty → "primary" (always — body itself carries content)
        - body empty + title matches normative-verb regex → "primary"
          (title IS the assertion, e.g., "Device shall support n41")
        - body empty + title is long (>= marker_max_title_chars) →
          "primary" (likely a sentence even without "shall")
        - otherwise → "marker"
        """
        if not self._is_effectively_empty(body):
            return CHUNK_ROLE_PRIMARY, "non_empty_body"
        title_stripped = (title or "").strip()
        if self._normative_re.search(title_stripped):
            return CHUNK_ROLE_PRIMARY, "title_has_normative_verb"
        if len(title_stripped) >= self.config.marker_max_title_chars:
            return CHUNK_ROLE_PRIMARY, "title_long_enough"
        return CHUNK_ROLE_MARKER, "empty_body_short_uninformative_title"

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

        # Optional CSV log of every chunk classified "marker". Opened
        # in write-truncate mode so each `build_chunks` run starts
        # fresh; closed at end of this method.
        marker_log_path = self.config.skipped_headers_log
        marker_log_file = None
        marker_log_writer = None
        if marker_log_path:
            try:
                p = Path(marker_log_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                marker_log_file = open(p, "w", newline="", encoding="utf-8")
                marker_log_writer = csv.writer(marker_log_file)
                marker_log_writer.writerow([
                    "plan_id", "plan_name", "section_number",
                    "hierarchy_path", "title", "req_id", "verdict", "reason",
                ])
            except Exception as e:
                logger.warning(
                    "Failed to open skipped_headers_log at %s (%s) — "
                    "continuing without marker log",
                    marker_log_path, e,
                )
                marker_log_file = None
                marker_log_writer = None

        chunks = []
        role_counts = {CHUNK_ROLE_PRIMARY: 0, CHUNK_ROLE_MARKER: 0}
        for tree in trees:
            tree_chunks = self._build_tree_chunks(
                tree, plan_features, marker_log_writer,
            )
            for c in tree_chunks:
                role = c.metadata.get("chunk_role", CHUNK_ROLE_PRIMARY)
                role_counts[role] = role_counts.get(role, 0) + 1
            chunks.extend(tree_chunks)

        if marker_log_file is not None:
            marker_log_file.close()
            logger.info(
                "Marker-header log written to %s (%d markers)",
                marker_log_path, role_counts.get(CHUNK_ROLE_MARKER, 0),
            )

        logger.info(
            "Built %d chunks from %d trees (primary=%d, marker=%d)",
            len(chunks), len(trees),
            role_counts.get(CHUNK_ROLE_PRIMARY, 0),
            role_counts.get(CHUNK_ROLE_MARKER, 0),
        )
        return chunks

    def _build_tree_chunks(
        self,
        tree: dict,
        plan_features: dict[str, list[str]],
        marker_log_writer: Any = None,
    ) -> list[Chunk]:
        """Build chunks for all requirements in one tree.

        marker_log_writer: optional csv.writer. When non-None, every
        chunk classified `marker` gets a row appended for audit.
        """
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

        # Build req_id → title AND req_id → body lookups once per
        # tree. title-lookup feeds the existing [Subsections: ...]
        # augmentation; text-lookup feeds the new [Parent context: ...]
        # augmentation (Tier-1 fix — see strand journal). Building both
        # in one pass keeps the per-tree pre-compute cheap.
        id_to_title: dict[str, str] = {}
        id_to_text: dict[str, str] = {}
        # section_number → (title, body) for ancestor-section Context
        # (D-DRAFT-5). In leading_id_body corpora the section nodes live in
        # `requirements` with an empty req_id (carrying section_number/title/
        # text); requirements themselves have section_number="" so they are
        # naturally excluded from this index.
        section_index: dict[str, tuple[str, str]] = {}
        for r in tree.get("requirements", []):
            rid = r.get("req_id", "")
            if rid:
                id_to_title[rid] = (r.get("title", "") or "").strip()
                id_to_text[rid] = (r.get("text", "") or "").strip()
            sn = (r.get("section_number", "") or "").strip()
            if sn:
                section_index[sn] = (
                    (r.get("title", "") or "").strip(),
                    (r.get("text", "") or "").strip(),
                )
        build_context = tree.get("build_context", "none") or "none"
        build_context_max_chars = int(tree.get("build_context_max_chars", 0) or 0)

        chunks = []
        for req in tree.get("requirements", []):
            req_id = req.get("req_id", "")
            if not req_id:
                continue

            # Classify chunk role (primary vs marker). The text-builder
            # uses id_to_text for parent-body lookup; classification is
            # independent of text-builder output (uses raw req fields).
            role, reason = (CHUNK_ROLE_PRIMARY, "skip_disabled")
            if self.config.skip_uninformative_headers:
                role, reason = self._classify_chunk_role(
                    req.get("title", ""), req.get("text", ""),
                )

            text = self._build_chunk_text(
                req, mno, release, plan_name, version,
                id_to_title, plan_id, id_to_text,
                build_context, section_index, build_context_max_chars,
            )

            # Skip chunks with no meaningful content (e.g., everything
            # got stripped). Distinct from `marker` — markers KEEP
            # their text and stay in the store; this branch drops the
            # chunk entirely because there's nothing to index OR cite.
            if not text.strip():
                continue

            # FR-35 [D-032]: inline-expand definitions on first occurrence
            # of each known term, except for chunks within the definitions
            # section itself (avoid double-anchoring).
            if defs_pattern is not None and not self._belongs_to_definitions(
                req, defs_section_num
            ):
                text = self._expand_definitions(text, defs_pattern, definitions_map)

            doc_root = plan_name or plan_id
            req_hier = req.get("hierarchy_path", []) or []
            full_hier: list[str] = ([doc_root] + req_hier) if doc_root else req_hier

            metadata = {
                "mno": mno,
                "release": release,
                "doc_type": "requirement",
                "plan_id": plan_id,
                "req_id": req_id,
                # True for actual requirements, False for structural section nodes
                # (a corpus may assign ids to both). Back-compat: trees without the
                # flag treat any req_id as a requirement.
                "is_requirement": req.get("is_requirement", bool(req_id)),
                "section_number": req.get("section_number", ""),
                "zone_type": req.get("zone_type", ""),
                "feature_ids": feature_ids,
                "hierarchy_path": full_hier,
                "chunk_role": role,
            }

            chunk_id = f"req:{req_id}"
            chunks.append(Chunk(chunk_id=chunk_id, text=text, metadata=metadata))

            # Log marker classification for audit. Caller can grep the
            # CSV to confirm no answer-bearing headings are being skipped
            # by the heuristic.
            if role == CHUNK_ROLE_MARKER and marker_log_writer is not None:
                marker_log_writer.writerow([
                    plan_id,
                    plan_name,
                    req.get("section_number", ""),
                    " > ".join(full_hier),
                    (req.get("title", "") or "").strip(),
                    req_id,
                    role,
                    reason,
                ])

        # One short, retrieval-friendly chunk per glossary entry
        # (D-043). The whole acronym table also lives inside the
        # definitions-section req chunk, but that chunk is dominated
        # by the other 18 entries — short queries like "What is X?"
        # rank it low. A dedicated chunk with the acronym in the
        # first ~80 chars wins on both BM25 (high TF-IDF) and dense
        # similarity (concise definition).
        #
        # Skipped when ``tree.embed_glossary == False`` (Phase 5 —
        # generic-rules pivot): the glossary section was already
        # dropped from ``requirements`` upstream; per-acronym chunks
        # would be the same content reaching RAG via a different
        # door. ``definitions_map`` survives so body chunks still get
        # acronym-expansion via ``_expand_definitions``.
        if tree.get("embed_glossary", True):
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
            doc_root = plan_name or plan_id
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
                    "hierarchy_path": [doc_root] if doc_root else [],
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
        plan_id: str = "",
        id_to_text: dict[str, str] | None = None,
        build_context: str = "none",
        section_index: dict[str, tuple[str, str]] | None = None,
        build_context_max_chars: int = 0,
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

        # Hierarchy path — document name is the root so the embedding
        # captures the full Document > Section > Subsection chain.
        if self.config.include_hierarchy_path:
            hierarchy = req.get("hierarchy_path", []) or []
            doc_root = plan_name or plan_id
            full_path = ([doc_root] + hierarchy) if doc_root else hierarchy
            if full_path:
                parts.append(f"[Path: {' > '.join(full_path)}]")

        # Requirement ID
        if self.config.include_req_id:
            req_id = req.get("req_id", "")
            if req_id:
                parts.append(f"[Req ID: {req_id}]")

        # Parent context (Tier-1 fix — see nora-retrieval-parent-displacement
        # strand). Prepends the parent requirement's body text so dense +
        # BM25 retrieval can match queries that semantically target the
        # parent's normative content even when the leaf's body uses
        # different vocabulary (e.g. parent: "device shall support the
        # following bands"; leaf: "n41 n78"). Capped at
        # parent_body_max_chars to bound chunk growth. Skipped when:
        # - include_parent_body is False (config opt-out)
        # - id_to_text lookup is not provided (callers that don't build it)
        # - no parent_req_id on the req
        # - parent body is empty / not in lookup
        if self.config.include_parent_body and id_to_text is not None:
            parent_id = req.get("parent_req_id", "")
            if parent_id:
                parent_body = id_to_text.get(parent_id, "").strip()
                if parent_body:
                    cap = self.config.parent_body_max_chars
                    if len(parent_body) > cap:
                        parent_body = parent_body[:cap] + "…"
                    parts.append(f"[Parent context: {parent_body}]")

        # Ancestor-section Context (D-DRAFT-5, strand multi-mno-nora). For
        # corpora whose sections are non-requirement context (leading_id_body
        # model), surface each enclosing section's heading + body so the chunk
        # is self-contained. Only emitted for `path_and_content` — the `path`
        # breadcrumb is already carried by the `[Path: …]` block above, so
        # emitting it here would duplicate it. Assembled by the shared
        # `build_context_string` helper from the tree's section nodes.
        if build_context == "path_and_content" and section_index is not None:
            ctx = build_context_string(
                req.get("parent_section", "") or "", section_index, build_context,
                max_chars=build_context_max_chars,
            )
            if ctx:
                parts.append(ctx)

        # Section title (always included — it's the heading)
        title = req.get("title", "")
        if title:
            parts.append(f"\n{title}")

        # Body text
        body = req.get("text", "")
        if body:
            parts.append(body)

        # Tables are inlined into req.text at their document position by the
        # parser (faithful order), so they're already in the body above — no
        # separate append here (that would duplicate them and lose position).
        # `include_tables` stays in config for back-compat but no longer gates
        # rendering; tables are intrinsic to the requirement text now.

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
        """Convert a table dict to Markdown. Delegates to the parser's canonical
        renderer so NORA chunks and the SIRA corpus render tables identically."""
        from core.src.parser.structural_parser import render_table_markdown
        return render_table_markdown(table.get("headers", []), table.get("rows", []))

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
