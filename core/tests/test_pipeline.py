"""Pipeline smoke tests — extract, profile, and parse real PDFs.

These tests run the full pipeline on actual VZW documents to verify
end-to-end correctness. They're slower than unit tests but catch
integration issues between the three steps.
"""

from pathlib import Path

import pytest

from src.extraction.registry import extract_document, supported_extensions
from src.models.document import BlockType, DocumentIR
from src.profiler.profiler import DocumentProfiler
from src.profiler.profile_schema import DocumentProfile
from src.parser.structural_parser import GenericStructuralParser


# Skip all tests if PDFs aren't available (e.g., CI without test data)
PDF_DIR = Path(".")
LTEDATARETRY = PDF_DIR / "LTEDATARETRY.pdf"
LTEB13NAC = PDF_DIR / "LTEB13NAC.pdf"

pytestmark = pytest.mark.skipif(
    not LTEDATARETRY.exists(), reason="PDF test data not available"
)


@pytest.fixture(scope="module")
def ltedataretry_ir() -> DocumentIR:
    return extract_document(LTEDATARETRY, mno="VZW", release="2026_feb", doc_type="requirement")


@pytest.fixture(scope="module")
def lteb13nac_ir() -> DocumentIR:
    return extract_document(LTEB13NAC, mno="VZW", release="2026_feb", doc_type="requirement")


@pytest.fixture(scope="module")
def vzw_profile(ltedataretry_ir: DocumentIR, lteb13nac_ir: DocumentIR) -> DocumentProfile:
    profiler = DocumentProfiler()
    return profiler.create_profile([ltedataretry_ir, lteb13nac_ir], profile_name="VZW_OA_test")


class TestExtraction:
    def test_produces_blocks(self, ltedataretry_ir: DocumentIR):
        assert ltedataretry_ir.block_count > 100

    def test_has_text_blocks(self, ltedataretry_ir: DocumentIR):
        text_blocks = ltedataretry_ir.blocks_by_type(BlockType.PARAGRAPH)
        assert len(text_blocks) > 50

    def test_has_tables(self, ltedataretry_ir: DocumentIR):
        tables = ltedataretry_ir.blocks_by_type(BlockType.TABLE)
        assert len(tables) > 0

    def test_blocks_have_font_info(self, ltedataretry_ir: DocumentIR):
        text_blocks = ltedataretry_ir.blocks_by_type(BlockType.PARAGRAPH)
        with_font = [b for b in text_blocks if b.font_info is not None]
        assert len(with_font) == len(text_blocks)

    def test_blocks_are_sorted_by_position(self, ltedataretry_ir: DocumentIR):
        blocks = ltedataretry_ir.content_blocks
        for i in range(1, len(blocks)):
            prev, curr = blocks[i - 1], blocks[i]
            assert (prev.position.page, prev.position.index) <= (curr.position.page, curr.position.index)

    def test_indices_are_sequential(self, ltedataretry_ir: DocumentIR):
        indices = [b.position.index for b in ltedataretry_ir.content_blocks]
        assert indices == list(range(len(indices)))

    def test_metadata_has_page_count(self, ltedataretry_ir: DocumentIR):
        assert ltedataretry_ir.extraction_metadata.get("page_count", 0) > 0

    def test_metadata_has_header_footer_patterns(self, ltedataretry_ir: DocumentIR):
        patterns = ltedataretry_ir.extraction_metadata.get("header_footer_patterns", [])
        assert isinstance(patterns, list)

    def test_ir_round_trips(self, ltedataretry_ir: DocumentIR, tmp_path: Path):
        json_path = tmp_path / "ir.json"
        ltedataretry_ir.save_json(json_path)
        loaded = DocumentIR.load_json(json_path)
        assert loaded.block_count == ltedataretry_ir.block_count
        assert loaded.source_file == ltedataretry_ir.source_file


class TestProfiling:
    def test_detects_body_text(self, vzw_profile: DocumentProfile):
        bt = vzw_profile.body_text
        assert bt.font_size_min > 0
        assert bt.font_size_max > bt.font_size_min
        assert len(bt.font_families) > 0

    def test_detects_heading_levels(self, vzw_profile: DocumentProfile):
        assert len(vzw_profile.heading_detection.levels) >= 1

    def test_detects_section_numbering(self, vzw_profile: DocumentProfile):
        assert vzw_profile.heading_detection.numbering_pattern != ""
        assert vzw_profile.heading_detection.max_observed_depth >= 3

    def test_detects_requirement_ids(self, vzw_profile: DocumentProfile):
        rid = vzw_profile.requirement_id
        assert rid.pattern != ""
        assert rid.total_found > 100
        assert len(rid.sample_ids) > 0

    def test_detects_plan_metadata_patterns(self, vzw_profile: DocumentProfile):
        pm = vzw_profile.plan_metadata
        assert pm.plan_id.pattern != ""
        assert pm.plan_name.pattern != ""

    def test_detects_document_zones(self, vzw_profile: DocumentProfile):
        assert len(vzw_profile.document_zones) > 0
        zone_types = {z.zone_type for z in vzw_profile.document_zones}
        assert "introduction" in zone_types

    def test_cross_ref_pattern_matches_req_id_pattern(self, vzw_profile: DocumentProfile):
        """Cross-ref req pattern should come from the detected req ID pattern."""
        assert vzw_profile.cross_reference_patterns.requirement_id_refs == vzw_profile.requirement_id.pattern

    def test_header_footer_from_extraction(self, vzw_profile: DocumentProfile):
        """Profiler should collect h/f patterns from extractor metadata, not re-detect."""
        hf = vzw_profile.header_footer
        assert hf.page_number_pattern != ""

    def test_profile_round_trips(self, vzw_profile: DocumentProfile, tmp_path: Path):
        json_path = tmp_path / "profile.json"
        vzw_profile.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)
        assert loaded.profile_name == vzw_profile.profile_name
        assert len(loaded.heading_detection.levels) == len(vzw_profile.heading_detection.levels)
        assert loaded.requirement_id.pattern == vzw_profile.requirement_id.pattern


class TestParsing:
    @pytest.fixture(scope="class")
    def tree(self, ltedataretry_ir: DocumentIR, vzw_profile: DocumentProfile):
        parser = GenericStructuralParser(vzw_profile)
        return parser.parse(ltedataretry_ir)

    def test_produces_requirements(self, tree):
        assert len(tree.requirements) > 10

    def test_extracts_plan_id(self, tree):
        assert tree.plan_id == "LTEDATARETRY"

    def test_extracts_plan_name(self, tree):
        assert tree.plan_name != ""

    def test_extracts_version(self, tree):
        assert tree.version != ""

    def test_requirements_have_section_numbers(self, tree):
        with_numbers = [r for r in tree.requirements if r.section_number]
        assert len(with_numbers) == len(tree.requirements)

    def test_requirements_have_hierarchy_paths(self, tree):
        with_paths = [r for r in tree.requirements if r.hierarchy_path]
        assert len(with_paths) > 0

    def test_parent_child_links_are_consistent(self, tree):
        by_section = {r.section_number: r for r in tree.requirements}
        for req in tree.requirements:
            if req.parent_section:
                parent = by_section.get(req.parent_section)
                assert parent is not None, f"Section {req.section_number} references missing parent {req.parent_section}"
                child_id = req.req_id or req.section_number
                assert child_id in parent.children, (
                    f"Section {req.section_number} claims parent {req.parent_section} "
                    f"but parent's children list doesn't include {child_id}"
                )

    def test_no_trailing_dots_in_spec_names(self, tree):
        for spec in tree.referenced_standards_releases:
            assert not spec.endswith("."), f"Spec name has trailing dot: {spec}"

    def test_referenced_standards_found(self, tree):
        assert len(tree.referenced_standards_releases) > 0

    def test_introduction_zone_detected(self, tree):
        intro_reqs = [r for r in tree.requirements if r.zone_type == "introduction"]
        assert len(intro_reqs) > 0


class TestRegistry:
    def test_supported_extensions_returns_pdf(self):
        exts = supported_extensions()
        assert ".pdf" in exts

    def test_supported_extensions_no_unimplemented(self):
        """Only extensions with actual extractors should be listed."""
        exts = supported_extensions()
        for ext in exts:
            # Each should have a working extractor — this just verifies
            # the registry isn't advertising formats it can't handle
            from src.extraction.registry import get_extractor
            extractor = get_extractor(Path(f"test{ext}"))
            assert extractor is not None
