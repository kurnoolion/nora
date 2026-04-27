"""Selective section extraction from parsed 3GPP specs (TDD 5.6, Step 2).

Given a parsed SpecDocument and a list of referenced section numbers,
extracts:
1. Each referenced section (primary content)
2. Parent section (structural context)
3. Sibling sub-sections of the same procedure
4. Definitions section (3.1) if any term lookups are needed

Produces a focused extract — typically 5-10% of the full spec.
No LLM required.
"""

from __future__ import annotations

import logging

from src.standards.schema import ExtractedSpecContent, SpecDocument, SpecSection

logger = logging.getLogger(__name__)


class SectionExtractor:
    """Extract referenced sections and surrounding context from a spec."""

    def extract(
        self,
        spec: SpecDocument,
        referenced_sections: list[str],
        source_plans: list[str] | None = None,
    ) -> ExtractedSpecContent:
        """Extract referenced sections with context.

        Args:
            spec: Parsed 3GPP spec document.
            referenced_sections: Section numbers referenced by MNO docs.
            source_plans: Plan IDs that reference this spec.

        Returns:
            ExtractedSpecContent with referenced and context sections.
        """
        if not referenced_sections:
            # No specific sections — include the full section index
            return ExtractedSpecContent(
                spec_number=spec.spec_number,
                release=spec.release,
                release_num=spec.release_num,
                version=spec.version,
                spec_title=spec.title,
                total_sections_in_spec=len(spec.sections),
                source_plans=source_plans or [],
            )

        # Build a section lookup map
        section_map = {s.number: s for s in spec.sections if s.number}

        # Collect referenced sections (deduplicated)
        primary_nums: set[str] = set()
        context_nums: set[str] = set()

        for sec_num in referenced_sections:
            sec = section_map.get(sec_num)
            if not sec:
                # Try prefix match (e.g., "5.5.1" might match "5.5.1.1")
                logger.debug(
                    f"Section {sec_num} not found in "
                    f"TS {spec.spec_number} — skipping"
                )
                continue

            primary_nums.add(sec_num)

            # Add parent for structural context
            if sec.parent_number and sec.parent_number in section_map:
                context_nums.add(sec.parent_number)

                # Add grandparent for deeper context on deep sections
                parent = section_map[sec.parent_number]
                if parent.parent_number and parent.parent_number in section_map:
                    context_nums.add(parent.parent_number)

            # Add immediate children (sub-sections of the referenced section)
            for child_num in sec.children:
                context_nums.add(child_num)

            # Add siblings (other children of the parent) for procedure context
            if sec.parent_number and sec.parent_number in section_map:
                parent = section_map[sec.parent_number]
                for sibling_num in parent.children:
                    context_nums.add(sibling_num)

        # Remove primary sections from context (no duplicates)
        context_nums -= primary_nums

        # Always include definitions section (3.1) if it exists
        if "3.1" in section_map and "3.1" not in primary_nums:
            context_nums.add("3.1")

        # Build output sections
        referenced = [
            section_map[n] for n in sorted(primary_nums)
            if n in section_map
        ]
        context = [
            section_map[n] for n in sorted(context_nums)
            if n in section_map
        ]

        result = ExtractedSpecContent(
            spec_number=spec.spec_number,
            release=spec.release,
            release_num=spec.release_num,
            version=spec.version,
            spec_title=spec.title,
            referenced_sections=referenced,
            context_sections=context,
            total_sections_in_spec=len(spec.sections),
            source_plans=source_plans or [],
        )

        total_chars = sum(len(s.text) for s in referenced + context)
        spec_chars = sum(len(s.text) for s in spec.sections)
        pct = (total_chars / spec_chars * 100) if spec_chars else 0

        logger.info(
            f"TS {spec.spec_number}: extracted {len(referenced)} referenced + "
            f"{len(context)} context sections "
            f"({total_chars:,} chars, {pct:.1f}% of spec)"
        )
        return result
