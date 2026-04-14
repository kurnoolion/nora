"""Context assembler (TDD 7.5).

Builds the LLM prompt context from retrieved chunks, augmented
with structural and relational information from the knowledge graph.

Context includes:
  - MNO/release provenance
  - Requirement text (from chunk)
  - Hierarchy path
  - Parent context (if available)
  - Standards text (from graph)
  - Cross-reference annotations
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from src.graph.schema import NodeType, EdgeType
from src.query.schema import (
    QueryType,
    RetrievedChunk,
    ChunkContext,
    StandardsContext,
    AssembledContext,
)

logger = logging.getLogger(__name__)

# ── System prompts by query type ────────────────────────────────

_CITATION_RULES = (
    "\n\nCITATION RULES (mandatory):\n"
    "- You MUST cite the exact requirement IDs (e.g., VZ_REQ_LTEDATARETRY_7748) from the "
    "provided context for every factual claim.\n"
    "- When a requirement references a 3GPP specification, cite it as "
    "'3GPP TS X.Y, Section Z' (e.g., 3GPP TS 24.301, Section 5.5.1.2.6).\n"
    "- Do NOT paraphrase without citing. Every substantive statement must trace back to "
    "a specific requirement ID.\n"
    "- Do NOT invent or fabricate requirement IDs. Only use IDs that appear in the context."
)

_SYSTEM_PROMPTS = {
    QueryType.SINGLE_DOC: (
        "You are an expert telecom requirements analyst. "
        "Answer the user's question using ONLY the provided requirement context. "
        "Structure your answer around the specific requirements, referencing each by its ID. "
        "If the context is insufficient, say so."
        + _CITATION_RULES
    ),
    QueryType.CROSS_DOC: (
        "You are an expert telecom requirements analyst. "
        "The user's question requires information from multiple requirement documents. "
        "Synthesize information across all provided documents. "
        "Reference each requirement by its exact ID. "
        "Note when requirements from different documents interact or depend on each other."
        + _CITATION_RULES
    ),
    QueryType.FEATURE_LEVEL: (
        "You are an expert telecom requirements analyst. "
        "The user is asking about a telecom feature or capability. "
        "Summarize all requirements related to this feature across the provided documents. "
        "Reference each requirement by its exact ID and note which plan/document each comes from."
        + _CITATION_RULES
    ),
    QueryType.STANDARDS_COMPARISON: (
        "You are an expert telecom requirements analyst comparing MNO requirements "
        "with 3GPP standards. "
        "For each relevant requirement, explain how the MNO's requirement relates to "
        "the 3GPP standard — does it defer to, constrain, override, or extend the standard? "
        "Reference each requirement by its exact ID and cite 3GPP section numbers."
        + _CITATION_RULES
    ),
    QueryType.CROSS_MNO_COMPARISON: (
        "You are comparing MNO device requirements across operators. "
        "Present a structured comparison highlighting commonalities and differences. "
        "Use table format when appropriate. "
        "Reference each requirement by its exact ID from each MNO."
        + _CITATION_RULES
    ),
    QueryType.GENERAL: (
        "You are an expert telecom requirements analyst. "
        "Answer the user's question using the provided requirement context. "
        "Reference each requirement by its exact ID for every factual claim. "
        "If the context is insufficient, say so."
        + _CITATION_RULES
    ),
}


class ContextBuilder:
    """Assembles LLM prompt context from retrieved chunks and graph data."""

    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph

    def build(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        query_type: QueryType,
        max_context_chars: int = 30000,
    ) -> AssembledContext:
        """Build assembled context for LLM synthesis.

        Args:
            query: Original user query.
            chunks: Retrieved and ranked chunks.
            query_type: Classified query type.
            max_context_chars: Maximum context length (approximate).

        Returns:
            AssembledContext with system prompt and formatted context.
        """
        # Enrich each chunk with graph context
        enriched = [self._enrich_chunk(chunk) for chunk in chunks]

        # Build context text
        context_text = self._format_context(query, enriched, query_type)

        # Trim if too long
        if len(context_text) > max_context_chars:
            context_text = context_text[:max_context_chars] + "\n\n[Context truncated]"

        system_prompt = _SYSTEM_PROMPTS.get(
            query_type, _SYSTEM_PROMPTS[QueryType.GENERAL]
        )

        assembled = AssembledContext(
            system_prompt=system_prompt,
            context_text=context_text,
            chunks=enriched,
            query_type=query_type,
        )

        logger.info(
            f"Context assembled: {len(enriched)} chunks, "
            f"~{len(context_text)} chars, type={query_type.value}"
        )
        return assembled

    def _enrich_chunk(self, chunk: RetrievedChunk) -> ChunkContext:
        """Enrich a chunk with graph context (hierarchy, standards, etc.)."""
        node_id = chunk.graph_node_id
        hierarchy_path = []
        parent_text = ""
        standards = []
        related_ids = []

        if node_id in self._graph:
            data = self._graph.nodes[node_id]
            hierarchy_path = data.get("hierarchy_path", [])
            if isinstance(hierarchy_path, str):
                hierarchy_path = [hierarchy_path]

            # Get parent context
            parent_text = self._get_parent_text(node_id)

            # Get standards context
            standards = self._get_standards_context(node_id)

            # Get related requirement IDs (depends_on targets)
            related_ids = self._get_related_ids(node_id)

        return ChunkContext(
            chunk=chunk,
            hierarchy_path=hierarchy_path,
            parent_text=parent_text,
            standards=standards,
            related_chunk_ids=related_ids,
        )

    def _get_parent_text(self, node_id: str) -> str:
        """Get the parent requirement's text for context."""
        for source, _, edata in self._graph.in_edges(node_id, data=True):
            if edata.get("edge_type") == EdgeType.PARENT_OF.value:
                parent_data = self._graph.nodes.get(source, {})
                parent_text = parent_data.get("text", "")
                parent_title = parent_data.get("title", "")
                if parent_text:
                    return f"{parent_title}: {parent_text}"
                elif parent_title:
                    return parent_title
        return ""

    def _get_standards_context(self, node_id: str) -> list[StandardsContext]:
        """Get standards sections referenced by this requirement."""
        stds = []
        for _, target, edata in self._graph.out_edges(node_id, data=True):
            if edata.get("edge_type") != EdgeType.REFERENCES_STANDARD.value:
                continue

            tdata = self._graph.nodes.get(target, {})
            if tdata.get("node_type") != NodeType.STANDARD_SECTION.value:
                continue

            text = tdata.get("text", "")
            if not text:
                continue

            stds.append(StandardsContext(
                spec=tdata.get("spec", ""),
                section=tdata.get("section", ""),
                release_num=tdata.get("release_num", 0),
                title=tdata.get("title", ""),
                text=text[:2000],  # Limit individual standards text
            ))

        return stds

    def _get_related_ids(self, node_id: str) -> list[str]:
        """Get IDs of related requirement nodes (via depends_on)."""
        related = []
        for _, target, edata in self._graph.out_edges(node_id, data=True):
            if edata.get("edge_type") == EdgeType.DEPENDS_ON.value:
                tdata = self._graph.nodes.get(target, {})
                req_id = tdata.get("req_id", "")
                if req_id:
                    related.append(req_id)
        return related

    def _format_context(
        self,
        query: str,
        chunks: list[ChunkContext],
        query_type: QueryType,
    ) -> str:
        """Format enriched chunks into a context string for the LLM."""
        parts = [f"User Question: {query}\n"]
        parts.append("=" * 60)
        parts.append("CONTEXT FROM REQUIREMENT DOCUMENTS")
        parts.append("=" * 60)

        for i, ctx in enumerate(chunks):
            chunk = ctx.chunk
            meta = chunk.metadata

            parts.append(f"\n--- Requirement {i + 1} of {len(chunks)} ---")

            # Provenance header
            mno = meta.get("mno", "")
            release = meta.get("release", "")
            plan_id = meta.get("plan_id", "")
            req_id = meta.get("req_id", "")
            section = meta.get("section_number", "")

            parts.append(
                f"MNO: {mno} | Release: {release} | "
                f"Plan: {plan_id} | Section: {section}"
            )
            parts.append(f"Req ID: {req_id}")

            # Hierarchy path
            if ctx.hierarchy_path:
                parts.append(f"Path: {' > '.join(ctx.hierarchy_path)}")

            # Parent context
            if ctx.parent_text:
                parts.append(f"Parent context: {ctx.parent_text[:500]}")

            # The requirement text (from chunk, already contextualized)
            # Strip the metadata headers since we're adding our own
            text = self._strip_chunk_headers(chunk.text)
            parts.append(f"\n{text}")

            # Standards context
            for std in ctx.standards:
                parts.append(
                    f"\n[Referenced Standard: 3GPP TS {std.spec}, "
                    f"Section {std.section} (Release {std.release_num})]"
                )
                if std.title:
                    parts.append(f"Title: {std.title}")
                parts.append(std.text[:1500])

            # Cross-reference annotations
            if ctx.related_chunk_ids:
                parts.append(
                    f"\nDepends on: {', '.join(ctx.related_chunk_ids[:5])}"
                )

            # Similarity score (useful for debugging)
            parts.append(f"[Relevance score: {chunk.similarity_score:.4f}]")

        # Add citation reminder at end of context (closest to generation)
        parts.append("\n" + "=" * 60)
        parts.append(
            "IMPORTANT: In your answer, you MUST reference the specific "
            "Req IDs (e.g., VZ_REQ_LTESMS_30328) shown above for each claim. "
            "Also cite any 3GPP TS specifications mentioned in the context."
        )
        parts.append("=" * 60)

        return "\n".join(parts)

    @staticmethod
    def _strip_chunk_headers(text: str) -> str:
        """Strip the contextualization headers from chunk text.

        The chunk text has [MNO: ...], [Path: ...], [Req ID: ...] headers
        added by ChunkBuilder. Since ContextBuilder adds its own headers,
        strip these to avoid duplication.
        """
        lines = text.split("\n")
        content_lines = []
        header_done = False

        for line in lines:
            if not header_done and line.startswith("[") and "]" in line:
                continue  # Skip header lines
            else:
                header_done = True
                content_lines.append(line)

        return "\n".join(content_lines).strip()
