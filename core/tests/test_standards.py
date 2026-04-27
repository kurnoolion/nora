"""Tests for the standards ingestion pipeline (Step 7).

Covers:
- Spec resolver: version encoding, URL building, release mapping
- Reference collector: manifest + tree text scanning
- Spec parser: section tree from DOCX
- Section extractor: referenced + context selection
- Schema serialization round-trips
"""

import json
from pathlib import Path

import pytest

from src.standards.reference_collector import (
    StandardsReferenceCollector,
    _clean_spec_number,
    _parse_release_num,
)
from src.standards.schema import (
    AggregatedSpecRef,
    ExtractedSpecContent,
    SpecDocument,
    SpecSection,
    StandardsReferenceIndex,
)
from src.standards.section_extractor import SectionExtractor
from src.standards.spec_resolver import (
    SpecResolver,
    build_candidate_urls,
    build_url,
    code_to_version,
    release_to_prefix,
    spec_to_compact,
    spec_to_series,
    version_to_code,
)


# ── Spec Resolver ─────────────────────────────────────────────────


class TestSpecResolver:
    def test_spec_to_series(self):
        assert spec_to_series("24.301") == "24"
        assert spec_to_series("36.331") == "36"
        assert spec_to_series("23.003") == "23"

    def test_spec_to_compact(self):
        assert spec_to_compact("24.301") == "24301"
        assert spec_to_compact("36.331") == "36331"

    def test_release_to_prefix(self):
        assert release_to_prefix(8) == "8"
        assert release_to_prefix(9) == "9"
        assert release_to_prefix(10) == "a"
        assert release_to_prefix(11) == "b"
        assert release_to_prefix(14) == "e"
        assert release_to_prefix(15) == "f"

    def test_version_to_code(self):
        assert version_to_code("8.7.0") == "870"
        assert version_to_code("9.5.0") == "950"
        assert version_to_code("11.7.0") == "b70"
        assert version_to_code("12.13.0") == "cd0"
        assert version_to_code("14.10.0") == "ea0"
        assert version_to_code("15.8.0") == "f80"

    def test_code_to_version(self):
        assert code_to_version("870") == "8.7.0"
        assert code_to_version("b70") == "11.7.0"
        assert code_to_version("cd0") == "12.13.0"
        assert code_to_version("ea0") == "14.10.0"
        assert code_to_version("f80") == "15.8.0"

    def test_version_roundtrip(self):
        for v in ["8.0.0", "9.5.0", "11.14.0", "12.3.0", "15.8.0"]:
            assert code_to_version(version_to_code(v)) == v

    def test_build_url(self):
        url = build_url("24.301", "be0")
        assert url == (
            "https://www.3gpp.org/ftp/Specs/archive/"
            "24_series/24.301/24301-be0.zip"
        )

    def test_build_url_different_series(self):
        url = build_url("36.331", "b50")
        assert "36_series/36.331/36331-b50.zip" in url

    def test_build_candidate_urls(self):
        candidates = build_candidate_urls("24.301", 11, max_minor=5)
        assert len(candidates) == 6  # minor 5 down to 0
        # First candidate should be highest minor
        assert candidates[0].version == "11.5.0"
        assert candidates[0].version_code == "b50"
        # Last should be minor 0
        assert candidates[-1].version == "11.0.0"
        assert candidates[-1].version_code == "b00"

    def test_build_candidate_urls_all_have_urls(self):
        candidates = build_candidate_urls("36.331", 9, max_minor=3)
        for c in candidates:
            assert c.url.startswith("https://")
            assert "36331" in c.url
            assert c.spec_number == "36.331"
            assert c.release_num == 9

    def test_invalid_release(self):
        candidates = build_candidate_urls("24.301", 99)
        assert len(candidates) == 0


# ── Reference Collector Helpers ───────────────────────────────────


class TestReferenceCollectorHelpers:
    def test_clean_spec_number(self):
        assert _clean_spec_number("3GPP TS 24.301") == "24.301"
        assert _clean_spec_number("3GPP TS 24.301.") == "24.301"
        assert _clean_spec_number("TS 36.331") == "36.331"
        assert _clean_spec_number("24.301") == "24.301"
        assert _clean_spec_number("  24.301.  ") == "24.301"

    def test_parse_release_num(self):
        assert _parse_release_num("Release 11") == 11
        assert _parse_release_num("Release 8") == 8
        assert _parse_release_num("Rel-15") == 15
        assert _parse_release_num("rel11") == 11
        assert _parse_release_num("") == 0
        assert _parse_release_num("unknown") == 0


# ── Reference Collector Integration ──────────────────────────────


MANIFESTS_DIR = Path("data/resolved")
TREES_DIR = Path("data/parsed")


@pytest.mark.skipif(
    not MANIFESTS_DIR.exists() or not TREES_DIR.exists(),
    reason="Resolved/parsed data not available",
)
class TestReferenceCollectorIntegration:
    @pytest.fixture(scope="class")
    def index(self):
        collector = StandardsReferenceCollector()
        return collector.collect(
            manifest_dir=MANIFESTS_DIR,
            trees_dir=TREES_DIR,
        )

    def test_finds_references(self, index):
        assert index.total_refs > 0
        assert index.total_unique_specs > 0

    def test_source_documents(self, index):
        assert len(index.source_documents) == 5

    def test_has_key_specs(self, index):
        spec_nums = {s.spec for s in index.specs}
        assert "24.301" in spec_nums
        assert "36.331" in spec_nums
        assert "36.101" in spec_nums

    def test_sections_extracted_from_text(self, index):
        """Section-level refs found by scanning requirement text."""
        specs_with_sections = [s for s in index.specs if s.sections]
        assert len(specs_with_sections) > 0

    def test_24_301_has_sections(self, index):
        ts24301 = [s for s in index.specs if s.spec == "24.301" and s.sections]
        assert len(ts24301) > 0
        all_sections = []
        for s in ts24301:
            all_sections.extend(s.sections)
        assert "5.5.1.2.5" in all_sections


# ── Spec Parser ───────────────────────────────────────────────────


SPEC_DOCX = Path("data/standards/TS_24.301/Rel-11/24301-be0.docx")


@pytest.mark.skipif(
    not SPEC_DOCX.exists(),
    reason="TS 24.301 DOCX not available (run standards_cli first)",
)
class TestSpecParser:
    @pytest.fixture(scope="class")
    def spec(self):
        from src.standards.spec_parser import SpecParser
        return SpecParser().parse(SPEC_DOCX)

    def test_metadata(self, spec):
        assert spec.spec_number == "24.301"
        assert spec.version == "11.14.0"
        assert spec.release_num == 11

    def test_sections_parsed(self, spec):
        assert len(spec.sections) > 100

    def test_section_lookup(self, spec):
        sec = spec.get_section("5.5.1.2.5")
        assert sec is not None
        assert "Attach" in sec.title
        assert len(sec.text) > 0

    def test_section_ancestry(self, spec):
        ancestors = spec.get_section_with_ancestors("5.5.1.2.5")
        assert len(ancestors) == 5
        assert ancestors[0].number == "5"
        assert ancestors[-1].number == "5.5.1.2.5"

    def test_parent_child_links(self, spec):
        sec = spec.get_section("5.5.1")
        assert sec is not None
        assert len(sec.children) > 0
        # Children should reference back
        for child_num in sec.children:
            child = spec.get_section(child_num)
            assert child is not None
            assert child.parent_number == "5.5.1"

    def test_definitions_section(self, spec):
        defs = spec.get_section("3.1")
        assert defs is not None
        assert "Definitions" in defs.title


# ── Section Extractor ─────────────────────────────────────────────


class TestSectionExtractor:
    def _make_spec(self) -> SpecDocument:
        """Build a minimal spec with known sections for testing."""
        sections = [
            SpecSection(number="3.1", title="Definitions", depth=2,
                        text="Term A: definition A.", parent_number="3"),
            SpecSection(number="3", title="Definitions and abbreviations",
                        depth=1, text="", parent_number="",
                        children=["3.1", "3.2"]),
            SpecSection(number="3.2", title="Abbreviations", depth=2,
                        text="UE: User Equipment", parent_number="3"),
            SpecSection(number="5", title="Procedures", depth=1,
                        text="", parent_number="",
                        children=["5.1", "5.2"]),
            SpecSection(number="5.1", title="Attach", depth=2,
                        text="Attach overview.", parent_number="5",
                        children=["5.1.1", "5.1.2"]),
            SpecSection(number="5.1.1", title="Attach request", depth=3,
                        text="The UE sends ATTACH REQUEST.",
                        parent_number="5.1"),
            SpecSection(number="5.1.2", title="Attach accept", depth=3,
                        text="The network sends ATTACH ACCEPT.",
                        parent_number="5.1"),
            SpecSection(number="5.2", title="Detach", depth=2,
                        text="Detach overview.", parent_number="5",
                        children=["5.2.1"]),
            SpecSection(number="5.2.1", title="Detach request", depth=3,
                        text="The UE sends DETACH REQUEST.",
                        parent_number="5.2"),
        ]
        return SpecDocument(
            spec_number="99.999",
            title="Test Spec",
            version="1.0.0",
            release="Release 1",
            release_num=1,
            sections=sections,
        )

    def test_extracts_referenced_section(self):
        spec = self._make_spec()
        extractor = SectionExtractor()
        result = extractor.extract(spec, ["5.1.1"])

        ref_nums = [s.number for s in result.referenced_sections]
        assert "5.1.1" in ref_nums

    def test_includes_parent_as_context(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, ["5.1.1"])

        ctx_nums = [s.number for s in result.context_sections]
        assert "5.1" in ctx_nums  # parent

    def test_includes_siblings_as_context(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, ["5.1.1"])

        ctx_nums = [s.number for s in result.context_sections]
        assert "5.1.2" in ctx_nums  # sibling

    def test_includes_definitions(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, ["5.1.1"])

        ctx_nums = [s.number for s in result.context_sections]
        assert "3.1" in ctx_nums

    def test_no_duplicate_between_referenced_and_context(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, ["5.1.1", "5.1"])

        ref_nums = {s.number for s in result.referenced_sections}
        ctx_nums = {s.number for s in result.context_sections}
        assert ref_nums & ctx_nums == set()

    def test_empty_sections_returns_empty_result(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, [])
        assert len(result.referenced_sections) == 0
        assert len(result.context_sections) == 0

    def test_nonexistent_section_skipped(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(spec, ["99.99"])
        assert len(result.referenced_sections) == 0

    def test_source_plans_preserved(self):
        spec = self._make_spec()
        result = SectionExtractor().extract(
            spec, ["5.1.1"], source_plans=["PLAN_A", "PLAN_B"]
        )
        assert result.source_plans == ["PLAN_A", "PLAN_B"]


# ── Schema Serialization ─────────────────────────────────────────


class TestStandardsSchemaSerialization:
    def test_reference_index_round_trip(self, tmp_path):
        index = StandardsReferenceIndex(
            specs=[
                AggregatedSpecRef(
                    spec="24.301", release="Release 11",
                    release_num=11, sections=["5.5.1", "5.5.2"],
                    source_plans=["PLAN_A"], ref_count=10,
                )
            ],
            total_refs=10,
            total_unique_specs=1,
            source_documents=["PLAN_A"],
        )
        path = tmp_path / "index.json"
        index.save_json(path)
        loaded = StandardsReferenceIndex.load_json(path)

        assert loaded.total_refs == 10
        assert len(loaded.specs) == 1
        assert loaded.specs[0].spec == "24.301"
        assert loaded.specs[0].sections == ["5.5.1", "5.5.2"]

    def test_spec_document_round_trip(self, tmp_path):
        doc = SpecDocument(
            spec_number="24.301",
            title="NAS",
            version="11.14.0",
            release="Release 11",
            release_num=11,
            sections=[
                SpecSection(
                    number="5.1", title="Overview", depth=2,
                    text="Overview text.", parent_number="5",
                    children=["5.1.1"],
                )
            ],
        )
        path = tmp_path / "spec.json"
        doc.save_json(path)
        loaded = SpecDocument.load_json(path)

        assert loaded.spec_number == "24.301"
        assert loaded.version == "11.14.0"
        assert len(loaded.sections) == 1
        assert loaded.sections[0].number == "5.1"
        assert loaded.sections[0].children == ["5.1.1"]

    def test_extracted_content_round_trip(self, tmp_path):
        content = ExtractedSpecContent(
            spec_number="24.301",
            release="Release 11",
            release_num=11,
            version="11.14.0",
            spec_title="NAS",
            referenced_sections=[
                SpecSection(number="5.1", title="Overview", text="abc")
            ],
            context_sections=[
                SpecSection(number="3.1", title="Definitions", text="def")
            ],
            total_sections_in_spec=100,
            source_plans=["PLAN_A"],
        )
        path = tmp_path / "content.json"
        content.save_json(path)
        loaded = ExtractedSpecContent.load_json(path)

        assert loaded.spec_number == "24.301"
        assert len(loaded.referenced_sections) == 1
        assert len(loaded.context_sections) == 1
        assert loaded.source_plans == ["PLAN_A"]
