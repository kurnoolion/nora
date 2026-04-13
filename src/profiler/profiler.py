"""DocumentProfiler — standalone, LLM-free document structure analysis.

Analyzes representative documents to derive a document structure profile
that drives the generic structural parser. Pure heuristic/algorithmic:
font clustering, regex mining, frequency analysis.

See TDD Section 5.2 for the full design.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date
from pathlib import Path

from src.models.document import BlockType, ContentBlock, DocumentIR, FontInfo
from src.profiler.profile_schema import (
    BodyText,
    CrossReferencePatterns,
    DocumentProfile,
    DocumentZone,
    HeaderFooter,
    HeadingDetection,
    HeadingLevel,
    MetadataField,
    PlanMetadata,
    RequirementIdPattern,
)

logger = logging.getLogger(__name__)


class DocumentProfiler:
    """Derive document structure profiles from representative documents.

    Standalone module — no LLM dependency. Operates on normalized
    DocumentIR from the extraction layer.
    """

    def create_profile(
        self,
        docs: list[DocumentIR],
        profile_name: str = "",
    ) -> DocumentProfile:
        """Create a new profile from representative documents."""
        logger.info(
            f"Creating profile from {len(docs)} document(s): "
            f"{[d.source_file for d in docs]}"
        )

        all_text_blocks = []
        for doc in docs:
            all_text_blocks.extend(doc.blocks_by_type(BlockType.PARAGRAPH))

        body = self._detect_body_text(all_text_blocks)
        heading = self._detect_headings(all_text_blocks, body)
        req_id = self._detect_requirement_ids(docs)
        plan_meta = self._detect_plan_metadata(docs)
        zones = self._detect_document_zones(docs, heading)
        hf = self._detect_header_footer(docs)
        xrefs = self._detect_cross_references(docs)

        profile = DocumentProfile(
            profile_name=profile_name or self._derive_profile_name(docs),
            profile_version=1,
            created_from=[d.source_file for d in docs],
            last_updated=date.today().isoformat(),
            heading_detection=heading,
            requirement_id=req_id,
            plan_metadata=plan_meta,
            document_zones=zones,
            header_footer=hf,
            cross_reference_patterns=xrefs,
            body_text=body,
        )

        self._log_profile_summary(profile)
        return profile

    def update_profile(
        self,
        profile: DocumentProfile,
        docs: list[DocumentIR],
    ) -> DocumentProfile:
        """Update an existing profile with additional representative documents."""
        logger.info(
            f"Updating profile '{profile.profile_name}' with "
            f"{len(docs)} additional document(s)"
        )

        # Merge the new doc names
        new_sources = [d.source_file for d in docs]
        profile.created_from = list(set(profile.created_from + new_sources))
        profile.profile_version += 1
        profile.last_updated = date.today().isoformat()

        # Re-derive with all known patterns + new data
        # For now, re-analyze the new docs and merge findings
        all_text_blocks = []
        for doc in docs:
            all_text_blocks.extend(doc.blocks_by_type(BlockType.PARAGRAPH))

        # Update requirement IDs — add any new patterns found
        new_req = self._detect_requirement_ids(docs)
        if new_req.total_found > 0:
            existing_samples = set(profile.requirement_id.sample_ids)
            for sid in new_req.sample_ids:
                if sid not in existing_samples:
                    profile.requirement_id.sample_ids.append(sid)
            profile.requirement_id.total_found += new_req.total_found

        # Update cross-reference patterns — add any new ones
        new_xrefs = self._detect_cross_references(docs)
        existing_std = set(profile.cross_reference_patterns.standards_citations)
        for pat in new_xrefs.standards_citations:
            if pat not in existing_std:
                profile.cross_reference_patterns.standards_citations.append(pat)

        # Update document zones if new top-level sections found
        new_zones = self._detect_document_zones(docs, profile.heading_detection)
        existing_zone_patterns = {z.section_pattern for z in profile.document_zones}
        for z in new_zones:
            if z.section_pattern not in existing_zone_patterns:
                profile.document_zones.append(z)

        self._log_profile_summary(profile)
        return profile

    def validate_profile(
        self,
        profile: DocumentProfile,
        doc: DocumentIR,
    ) -> dict:
        """Validate a profile against a document. Returns a report dict."""
        logger.info(f"Validating profile against {doc.source_file}")

        text_blocks = doc.blocks_by_type(BlockType.PARAGRAPH)
        report: dict = {
            "document": doc.source_file,
            "profile": profile.profile_name,
            "total_blocks": doc.block_count,
            "text_blocks": len(text_blocks),
        }

        # Check heading detection
        heading_count = 0
        heading_by_level: Counter = Counter()
        for b in text_blocks:
            if not b.font_info:
                continue
            for lv in profile.heading_detection.levels:
                if (lv.font_size_min <= b.font_info.size <= lv.font_size_max
                        and (lv.bold is None or b.font_info.bold == lv.bold)):
                    heading_count += 1
                    heading_by_level[lv.level] += 1
                    break
        report["headings_detected"] = heading_count
        report["headings_by_level"] = dict(heading_by_level)

        # Check requirement IDs
        req_count = 0
        if profile.requirement_id.pattern:
            pattern = re.compile(profile.requirement_id.pattern)
            for b in doc.content_blocks:
                req_count += len(pattern.findall(b.text))
        report["requirement_ids_found"] = req_count

        # Check section numbering
        section_count = 0
        max_depth = 0
        if profile.heading_detection.numbering_pattern:
            num_re = re.compile(profile.heading_detection.numbering_pattern)
            for b in text_blocks:
                m = num_re.match(b.text)
                if m:
                    section_count += 1
                    depth = m.group(0).strip().count(".") + 1
                    max_depth = max(max_depth, depth)
        report["sections_with_numbers"] = section_count
        report["max_section_depth"] = max_depth

        # Check plan metadata extraction
        meta_found = {}
        first_page_text = " ".join(
            b.text for b in text_blocks if b.position.page == 1
        )
        for field_name in ["plan_name", "plan_id", "version", "release_date"]:
            mf: MetadataField = getattr(profile.plan_metadata, field_name)
            if mf.pattern:
                m = re.search(mf.pattern, first_page_text)
                meta_found[field_name] = m.group(1) if m else None
        report["plan_metadata"] = meta_found

        # Check header/footer coverage
        hf_match_count = 0
        if profile.header_footer.page_number_pattern:
            pn_re = re.compile(profile.header_footer.page_number_pattern)
            for b in text_blocks:
                if pn_re.search(b.text):
                    hf_match_count += 1
        report["header_footer_matches"] = hf_match_count

        return report

    # ----------------------------------------------------------------
    # Heuristic analysis methods
    # ----------------------------------------------------------------

    def _detect_body_text(self, text_blocks: list[ContentBlock]) -> BodyText:
        """Identify body text characteristics by frequency analysis.

        Body text is the most common font size among blocks with
        substantial text content.
        """
        size_counter: Counter = Counter()
        font_families: Counter = Counter()

        for b in text_blocks:
            if not b.font_info or len(b.text.strip()) < 10:
                continue
            size_counter[b.font_info.size] += 1
            if b.font_info.font_name:
                base_family = b.font_info.font_name.split(",")[0].strip()
                font_families[base_family] += 1

        if not size_counter:
            return BodyText()

        # Body text = most frequent font size (among substantial blocks)
        body_size = size_counter.most_common(1)[0][0]
        # Allow a small range around the body size
        body_min = body_size - 0.5
        body_max = body_size + 0.5
        top_families = [f for f, _ in font_families.most_common(3)]

        logger.info(f"Body text: {body_size}pt, families: {top_families}")
        return BodyText(
            font_size_min=body_min,
            font_size_max=body_max,
            font_families=top_families,
        )

    def _detect_headings(
        self,
        text_blocks: list[ContentBlock],
        body: BodyText,
    ) -> HeadingDetection:
        """Detect heading levels by font size clustering.

        Headings are text blocks with font size significantly larger
        than body text. Since VZW docs use a single heading font size,
        the hierarchy comes from section numbering depth, not font variation.
        """
        # Collect font size + bold combos for blocks larger than body text
        body_mid = (body.font_size_min + body.font_size_max) / 2
        heading_candidates: Counter = Counter()
        heading_samples: dict[tuple, list[str]] = {}

        for b in text_blocks:
            if not b.font_info or not b.text.strip():
                continue
            # Skip very short fragments — likely table cell spillover, not headings
            if len(b.text.strip()) < 8:
                continue
            size = b.font_info.size
            # Heading candidates: larger than body text
            if size > body_mid + 1.0:
                key = (round(size, 1), b.font_info.bold, b.font_info.all_caps)
                heading_candidates[key] += 1
                if key not in heading_samples:
                    heading_samples[key] = []
                if len(heading_samples[key]) < 5:
                    heading_samples[key].append(b.text[:80])

        # Sort by font size descending — largest = highest heading level
        sorted_candidates = sorted(
            heading_candidates.items(),
            key=lambda x: (-x[0][0], -x[1]),
        )

        levels = []
        level_num = 1
        for (size, bold, all_caps), count in sorted_candidates:
            if count < 3:  # skip very rare font combos
                continue
            levels.append(
                HeadingLevel(
                    level=level_num,
                    font_size_min=size - 0.5,
                    font_size_max=size + 0.5,
                    bold=bold if bold else None,
                    all_caps=all_caps if all_caps else None,
                    sample_texts=heading_samples.get(
                        (size, bold, all_caps), []
                    ),
                    count=count,
                )
            )
            level_num += 1

        # Detect section numbering pattern from heading text
        numbering_pattern, max_depth = self._detect_section_numbering(
            text_blocks, body_mid
        )

        heading = HeadingDetection(
            method="font_size_clustering",
            levels=levels,
            numbering_pattern=numbering_pattern,
            max_observed_depth=max_depth,
        )

        logger.info(
            f"Headings: {len(levels)} level(s), "
            f"numbering depth {max_depth}, "
            f"pattern: {numbering_pattern}"
        )
        return heading

    def _detect_section_numbering(
        self,
        text_blocks: list[ContentBlock],
        body_mid_size: float,
    ) -> tuple[str, int]:
        """Detect section numbering scheme from heading text."""
        # Look at text in blocks with font size > body
        section_number_re = re.compile(r"^((?:\d+\.)+\d*)\s")
        depths = []

        for b in text_blocks:
            if not b.font_info or b.font_info.size <= body_mid_size:
                continue
            m = section_number_re.match(b.text.strip())
            if m:
                num = m.group(1).rstrip(".")
                depth = num.count(".") + 1
                depths.append(depth)

        if not depths:
            return "", 0

        max_depth = max(depths)
        # Build the numbering regex pattern
        pattern = r"^(\d+\.)+\d*\s"
        return pattern, max_depth

    def _detect_requirement_ids(
        self, docs: list[DocumentIR]
    ) -> RequirementIdPattern:
        """Mine requirement ID patterns from document text."""
        # Try common MNO requirement ID patterns
        candidate_patterns = [
            (r"VZ_REQ_[A-Z0-9_]+_\d+", "VZ_REQ"),
            (r"ATT_REQ_[A-Z0-9_]+_\d+", "ATT_REQ"),
            (r"TMO_REQ_[A-Z0-9_]+_\d+", "TMO_REQ"),
            # Generic: PREFIX_WORD_NUMBER
            (r"[A-Z]{2,}_REQ_[A-Z0-9_]+_\d+", "GENERIC_REQ"),
        ]

        best_pattern = ""
        best_ids: list[str] = []
        best_count = 0

        for pattern, prefix in candidate_patterns:
            regex = re.compile(pattern)
            all_ids: list[str] = []
            for doc in docs:
                for b in doc.content_blocks:
                    all_ids.extend(regex.findall(b.text))

            if len(all_ids) > best_count:
                best_count = len(all_ids)
                best_pattern = pattern
                best_ids = all_ids

        if not best_ids:
            logger.warning("No requirement ID patterns detected")
            return RequirementIdPattern()

        unique_ids = sorted(set(best_ids))
        # Extract component structure from samples
        sample = unique_ids[0]
        parts = sample.split("_")
        components = {}
        if len(parts) >= 4 and parts[0] == "VZ" and parts[1] == "REQ":
            components = {
                "prefix": "VZ_REQ",
                "separator": "_",
                "plan_id_position": 2,
                "number_position": 3,
            }

        # Get a diverse sample of IDs (different plan IDs)
        plan_ids_seen: set[str] = set()
        diverse_samples: list[str] = []
        for rid in unique_ids:
            m = re.match(r"VZ_REQ_([A-Z0-9]+)_\d+", rid)
            if m and m.group(1) not in plan_ids_seen:
                plan_ids_seen.add(m.group(1))
                diverse_samples.append(rid)
            if len(diverse_samples) >= 5:
                break

        logger.info(
            f"Requirement IDs: {best_count} found, "
            f"{len(unique_ids)} unique, pattern: {best_pattern}"
        )

        return RequirementIdPattern(
            pattern=best_pattern,
            components=components,
            sample_ids=diverse_samples or unique_ids[:5],
            total_found=best_count,
        )

    def _detect_plan_metadata(
        self, docs: list[DocumentIR]
    ) -> PlanMetadata:
        """Detect plan metadata patterns from first-page content."""
        # Patterns to try against first-page text
        field_patterns = {
            "plan_name": [
                r"Plan\s+Name:\s*(.+?)(?:\n|Plan\s+Id|$)",
                r"Plan\s+Name\s*[:=]\s*(\S+)",
            ],
            "plan_id": [
                r"Plan\s+Id:\s*(\w+)",
                r"Plan\s+ID:\s*(\w+)",
            ],
            "version": [
                r"Version\s+Number:\s*([\d.]+)",
                r"Version:\s*([\d.]+)",
            ],
            "release_date": [
                r"Release\s+Date:\s*(.+?)(?:\n|Latest|$)",
            ],
        }

        results: dict[str, MetadataField] = {}

        for field_name, patterns in field_patterns.items():
            best_match = None
            best_pattern = ""
            match_count = 0

            for pattern in patterns:
                regex = re.compile(pattern)
                for doc in docs:
                    first_page_text = " ".join(
                        b.text
                        for b in doc.blocks_by_type(BlockType.PARAGRAPH)
                        if b.position.page == 1
                    )
                    m = regex.search(first_page_text)
                    if m:
                        match_count += 1
                        if not best_match:
                            best_match = m.group(1).strip()
                            best_pattern = pattern

            results[field_name] = MetadataField(
                location="first_page",
                pattern=best_pattern,
                sample_value=best_match or "",
            )

            if best_match:
                logger.info(f"Plan metadata '{field_name}': {best_match}")

        return PlanMetadata(
            plan_name=results.get("plan_name", MetadataField()),
            plan_id=results.get("plan_id", MetadataField()),
            version=results.get("version", MetadataField()),
            release_date=results.get("release_date", MetadataField()),
        )

    def _detect_document_zones(
        self,
        docs: list[DocumentIR],
        heading: HeadingDetection,
    ) -> list[DocumentZone]:
        """Classify top-level sections into document zones."""
        # Keywords that indicate zone types
        zone_keywords = {
            "introduction": ["introduction", "applicability", "acronyms", "glossary"],
            "hardware_specs": ["hardware", "mechanical", "electrical"],
            "software_specs": ["software", "specification", "algorithm", "timer"],
            "scenarios": ["scenario", "procedure"],
            "references": ["reference", "specification"],
            "test_coverage": ["test", "coverage", "testplan"],
            "change_history": ["change", "history", "revision", "version"],
        }

        # Find top-level headings (short section numbers like "1.1", "1.2")
        top_level_re = re.compile(r"^(\d+\.\d+)\s+(.+)")
        zones_seen: dict[str, DocumentZone] = {}

        for doc in docs:
            for b in doc.blocks_by_type(BlockType.PARAGRAPH):
                if not b.font_info:
                    continue
                # Check if this is a heading-level block
                is_heading = False
                for lv in heading.levels:
                    if (lv.font_size_min <= b.font_info.size <= lv.font_size_max
                            and (lv.bold is None or b.font_info.bold == lv.bold)):
                        is_heading = True
                        break
                if not is_heading:
                    continue

                m = top_level_re.match(b.text.strip())
                if not m:
                    continue

                section_num = m.group(1)
                heading_text = m.group(2).strip()

                if section_num in zones_seen:
                    continue

                # Classify by keywords
                text_lower = heading_text.lower()
                zone_type = "content"  # default
                for ztype, keywords in zone_keywords.items():
                    if any(kw in text_lower for kw in keywords):
                        zone_type = ztype
                        break

                zones_seen[section_num] = DocumentZone(
                    section_pattern=f"^{re.escape(section_num)}\\b",
                    zone_type=zone_type,
                    description=heading_text,
                    heading_text=heading_text,
                )

        zones = sorted(zones_seen.values(), key=lambda z: z.section_pattern)
        logger.info(f"Document zones: {len(zones)} top-level sections detected")
        return zones

    def _detect_header_footer(
        self, docs: list[DocumentIR]
    ) -> HeaderFooter:
        """Detect header/footer patterns across documents."""
        header_patterns = set()
        footer_patterns = set()

        # Look for text that appears on most pages
        for doc in docs:
            page_texts: dict[int, list[str]] = {}
            for b in doc.blocks_by_type(BlockType.PARAGRAPH):
                if not b.font_info:
                    continue
                pg = b.position.page
                if pg not in page_texts:
                    page_texts[pg] = []
                # Normalize: replace digits with #
                normalized = re.sub(r"\d+", "#", b.text.strip())
                if normalized and len(normalized) < 100:
                    page_texts[pg].append(normalized)

            # Find text appearing on >60% of pages
            total_pages = len(page_texts)
            if total_pages < 3:
                continue

            text_freq: Counter = Counter()
            for texts in page_texts.values():
                for t in set(texts):  # dedupe within page
                    text_freq[t] += 1

            threshold = total_pages * 0.6
            for text, count in text_freq.items():
                if count >= threshold:
                    # Classify as header or footer based on content
                    if re.search(r"page|#\s+of\s+#", text, re.IGNORECASE):
                        footer_patterns.add(text)
                    else:
                        header_patterns.add(text)

        return HeaderFooter(
            header_patterns=sorted(header_patterns),
            footer_patterns=sorted(footer_patterns),
            page_number_pattern=r"^\s*Page\s+\d+\s+of\s+\d+\s*$",
        )

    def _detect_cross_references(
        self,
        docs: list[DocumentIR],
    ) -> CrossReferencePatterns:
        """Detect cross-reference patterns in document text."""
        # Check for various standards citation formats
        standards_patterns = [
            r"3GPP\s+TS\s+[\d.]+(?:\s+[Ss]ection\s+[\d.]+)?",
            r"3GPP\s+TS\s+[\d.]+(?:\s+[Rr]elease\s+\d+)?",
            r"GSMA\s+\w+[\d.]*",
            r"OMA\s+\w+[\d.]*",
        ]

        found_patterns: list[str] = []
        for pattern in standards_patterns:
            regex = re.compile(pattern)
            total_matches = 0
            for doc in docs:
                for b in doc.content_blocks:
                    total_matches += len(regex.findall(b.text))
            if total_matches > 0:
                found_patterns.append(pattern)
                logger.info(
                    f"Cross-ref pattern '{pattern}': {total_matches} matches"
                )

        # Internal section references
        section_ref_pattern = r"[Ss]ee\s+[Ss]ection\s+[\d.]+"
        section_ref_count = 0
        for doc in docs:
            for b in doc.content_blocks:
                section_ref_count += len(
                    re.findall(section_ref_pattern, b.text)
                )

        # Requirement ID references (reuse the detected pattern)
        req_pattern = ""
        req_count = 0
        for doc in docs:
            for b in doc.content_blocks:
                found = re.findall(r"VZ_REQ_[A-Z0-9_]+_\d+", b.text)
                req_count += len(found)
        if req_count > 0:
            req_pattern = r"VZ_REQ_[A-Z0-9_]+_\d+"

        return CrossReferencePatterns(
            standards_citations=found_patterns,
            internal_section_refs=section_ref_pattern
            if section_ref_count > 0
            else "",
            requirement_id_refs=req_pattern,
        )

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _derive_profile_name(docs: list[DocumentIR]) -> str:
        """Derive a profile name from the documents' MNO."""
        mnos = set(d.mno for d in docs if d.mno)
        if mnos:
            return f"{'_'.join(sorted(mnos))}_profile"
        return "document_profile"

    @staticmethod
    def _log_profile_summary(profile: DocumentProfile) -> None:
        logger.info(f"\n--- Profile Summary: {profile.profile_name} ---")
        logger.info(f"  Sources: {profile.created_from}")
        logger.info(
            f"  Heading levels: {len(profile.heading_detection.levels)}"
        )
        logger.info(
            f"  Section numbering depth: "
            f"{profile.heading_detection.max_observed_depth}"
        )
        logger.info(
            f"  Requirement ID pattern: {profile.requirement_id.pattern}"
        )
        logger.info(
            f"  Requirement IDs found: {profile.requirement_id.total_found}"
        )
        logger.info(f"  Document zones: {len(profile.document_zones)}")
        logger.info(
            f"  Body text: "
            f"{profile.body_text.font_size_min}-"
            f"{profile.body_text.font_size_max}pt"
        )
