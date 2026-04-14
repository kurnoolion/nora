"""LLM synthesizer (TDD 7.6).

Sends assembled context to the LLM and generates an answer
with citations. Uses the LLMProvider Protocol.

With MockLLMProvider, returns a structured summary of the context
(no actual LLM synthesis, but exercises the full pipeline).
"""

from __future__ import annotations

import json
import logging
import re

from src.query.schema import (
    AssembledContext,
    QueryResponse,
    QueryIntent,
    Citation,
)

logger = logging.getLogger(__name__)


class LLMSynthesizer:
    """Generates answers from assembled context using an LLM."""

    def __init__(self, llm_provider, max_tokens: int = 4096) -> None:
        self._llm = llm_provider
        self._max_tokens = max_tokens

    def synthesize(
        self,
        context: AssembledContext,
        intent: QueryIntent,
    ) -> QueryResponse:
        """Generate an answer from the assembled context.

        Args:
            context: Assembled context with system prompt and formatted text.
            intent: Original query intent (for metadata).

        Returns:
            QueryResponse with answer and citations.
        """
        prompt = context.context_text

        try:
            answer = self._llm.complete(
                prompt=prompt,
                system=context.system_prompt,
                temperature=0.0,
                max_tokens=self._max_tokens,
            )
        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            answer = f"[LLM synthesis failed: {e}]"

        # Extract citations from the answer
        citations = self._extract_citations(answer)

        response = QueryResponse(
            answer=answer,
            citations=citations,
            query_intent=intent,
            candidate_count=0,  # set by pipeline
            retrieved_count=len(context.chunks),
            context_tokens_approx=len(context.context_text) // 4,
        )

        logger.info(
            f"Synthesis complete: {len(answer)} chars, "
            f"{len(citations)} citations"
        )
        return response

    @staticmethod
    def _extract_citations(answer: str) -> list[Citation]:
        """Extract requirement and standards citations from the answer."""
        citations = []
        seen = set()

        # Requirement ID citations
        for m in re.finditer(r"(VZ_REQ_(\w+?)_(\d+))", answer):
            req_id = m.group(1)
            plan_id = m.group(2)
            if req_id not in seen:
                citations.append(Citation(
                    req_id=req_id,
                    plan_id=plan_id,
                ))
                seen.add(req_id)

        # Standards citations
        for m in re.finditer(
            r"3GPP\s+TS\s+(\d[\d.]*\d)(?:\s*,?\s*[Ss]ection\s+(\d[\d.]*\d))?",
            answer,
        ):
            spec = m.group(1)
            section = m.group(2) or ""
            key = f"{spec}:{section}"
            if key not in seen:
                citations.append(Citation(
                    spec=f"3GPP TS {spec}",
                    spec_section=section,
                ))
                seen.add(key)

        return citations


class MockSynthesizer:
    """Mock synthesizer that returns a structured summary without LLM.

    Useful for testing the full pipeline without API keys.
    Produces a deterministic summary of the retrieved context.
    """

    def synthesize(
        self,
        context: AssembledContext,
        intent: QueryIntent,
    ) -> QueryResponse:
        """Generate a mock answer summarizing the context."""
        chunks = context.chunks
        if not chunks:
            answer = (
                "No relevant requirements were found for this query. "
                "Try rephrasing or specifying a different plan or feature."
            )
            return QueryResponse(
                answer=answer,
                citations=[],
                query_intent=intent,
                retrieved_count=0,
            )

        # Build a structured summary
        parts = [f"Based on {len(chunks)} retrieved requirements:\n"]

        # Group by plan
        by_plan: dict[str, list] = {}
        for ctx in chunks:
            pid = ctx.chunk.metadata.get("plan_id", "unknown")
            by_plan.setdefault(pid, []).append(ctx)

        for pid, plan_chunks in by_plan.items():
            parts.append(f"\n## {pid} ({len(plan_chunks)} requirements)")
            for ctx in plan_chunks[:5]:  # Limit per plan
                meta = ctx.chunk.metadata
                req_id = meta.get("req_id", "?")
                section = meta.get("section_number", "?")
                title_line = ""
                # Extract title from chunk text
                text_lines = ctx.chunk.text.strip().split("\n")
                for line in text_lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("["):
                        title_line = stripped[:100]
                        break

                parts.append(f"- {req_id} (Section {section}): {title_line}")

                # Note standards references
                for std in ctx.standards[:2]:
                    parts.append(
                        f"  References: 3GPP TS {std.spec}, "
                        f"Section {std.section}"
                    )

        # Collect all citations
        citations = []
        seen = set()
        for ctx in chunks:
            req_id = ctx.chunk.metadata.get("req_id", "")
            plan_id = ctx.chunk.metadata.get("plan_id", "")
            if req_id and req_id not in seen:
                citations.append(Citation(req_id=req_id, plan_id=plan_id))
                seen.add(req_id)

            for std in ctx.standards:
                key = f"{std.spec}:{std.section}"
                if key not in seen:
                    citations.append(Citation(
                        spec=f"3GPP TS {std.spec}",
                        spec_section=std.section,
                    ))
                    seen.add(key)

        answer = "\n".join(parts)

        return QueryResponse(
            answer=answer,
            citations=citations,
            query_intent=intent,
            retrieved_count=len(chunks),
            context_tokens_approx=len(context.context_text) // 4,
        )
