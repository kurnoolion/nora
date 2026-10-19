"""Tests for DocumentProfile serialize/deserialize round-trip."""

from pathlib import Path

import pytest

from core.src.profiler.profile_schema import (
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
    TocDetection,
)


def _make_profile() -> DocumentProfile:
    return DocumentProfile(
        profile_name="test_profile",
        profile_version=2,
        created_from=["DOC_A.pdf", "DOC_B.pdf"],
        last_updated="2026-04-13",
        heading_detection=HeadingDetection(
            method="font_size_clustering",
            levels=[
                HeadingLevel(
                    level=1, font_size_min=13.5, font_size_max=14.5,
                    bold=True, all_caps=True,
                    sample_texts=["1.1 INTRODUCTION"], count=50,
                ),
                HeadingLevel(
                    level=2, font_size_min=13.5, font_size_max=14.5,
                    bold=None, all_caps=True,
                    sample_texts=["1.1.1 APPLICABILITY"], count=10,
                ),
            ],
            numbering_pattern=r"^(\d+\.)+\d*\s",
            max_observed_depth=5,
        ),
        requirement_id=RequirementIdPattern(
            pattern=r"VZ_REQ_[A-Z0-9_]+_\d+",
            components={"prefix": "VZ_REQ", "separator": "_", "plan_id_position": 2},
            sample_ids=["VZ_REQ_TEST_100", "VZ_REQ_TEST_200"],
            total_found=500,
            anchor="last_run",
            normalize="upper",
        ),
        plan_metadata=PlanMetadata(
            plan_name=MetadataField(location="first_page", pattern=r"Plan\s+Name:\s*(.+)", sample_value="Test_Plan"),
            plan_id=MetadataField(location="first_page", pattern=r"Plan\s+Id:\s*(\w+)", sample_value="TESTPLAN"),
            version=MetadataField(location="first_page", pattern=r"Version:\s*([\d.]+)", sample_value="5"),
            release_date=MetadataField(),
        ),
        document_zones=[
            DocumentZone(section_pattern=r"^1\.1\b", zone_type="introduction", heading_text="INTRODUCTION"),
            DocumentZone(section_pattern=r"^1\.3\b", zone_type="software_specs", heading_text="SOFTWARE"),
        ],
        header_footer=HeaderFooter(
            header_patterns=["Test Header #"],
            footer_patterns=[],
            page_number_pattern=r"^\s*Page\s+\d+\s+of\s+\d+\s*$",
        ),
        cross_reference_patterns=CrossReferencePatterns(
            standards_citations=[r"3GPP\s+TS\s+[\d.]+"],
            internal_section_refs=r"[Ss]ee\s+[Ss]ection\s+[\d.]+",
            requirement_id_refs=r"VZ_REQ_[A-Z0-9_]+_\d+",
        ),
        body_text=BodyText(font_size_min=11.5, font_size_max=12.5, font_families=["Fanwood"]),
        toc_detection=TocDetection(
            style_pattern=r"(?i)^toc\s+(\d+)$",
            entry_pattern=r"^(?P<num>[\w.]+)\t(?P<body>.+?)\t(?P<page>\d+)\s*$",
        ),
        revision_history_label_pattern=r"(?i)^\s*custom\s+history\s*$",
        definitions_table_term_column=r"(?i)^acronym$",
        definitions_table_definition_column=r"(?i)^expansion$",
    )


class TestDocumentProfileRoundTrip:
    def test_round_trip_top_level_fields(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        assert loaded.profile_name == "test_profile"
        assert loaded.profile_version == 2
        assert loaded.created_from == ["DOC_A.pdf", "DOC_B.pdf"]
        assert loaded.last_updated == "2026-04-13"

    def test_round_trip_heading_detection(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        hd = loaded.heading_detection
        assert hd.method == "font_size_clustering"
        assert hd.numbering_pattern == r"^(\d+\.)+\d*\s"
        assert hd.max_observed_depth == 5
        assert len(hd.levels) == 2

        lv1 = hd.levels[0]
        assert lv1.level == 1
        assert lv1.font_size_min == 13.5
        assert lv1.bold is True
        assert lv1.all_caps is True
        assert lv1.count == 50

        lv2 = hd.levels[1]
        assert lv2.bold is None
        assert lv2.all_caps is True

    def test_round_trip_requirement_id(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        rid = loaded.requirement_id
        assert rid.pattern == r"VZ_REQ_[A-Z0-9_]+_\d+"
        assert rid.components["prefix"] == "VZ_REQ"
        assert rid.components["plan_id_position"] == 2
        assert rid.total_found == 500
        assert len(rid.sample_ids) == 2
        assert rid.anchor == "last_run"
        assert rid.normalize == "upper"

    def test_requirement_id_defaults_preserve_legacy_behavior(self):
        """New ``anchor`` / ``normalize`` fields default to legacy values."""
        rid = RequirementIdPattern()
        assert rid.anchor == "trailing_text"
        assert rid.normalize == "none"

    def test_round_trip_toc_detection(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        toc = loaded.toc_detection
        assert toc.style_pattern == r"(?i)^toc\s+(\d+)$"
        assert "(?P<num>" in toc.entry_pattern
        assert "(?P<body>" in toc.entry_pattern
        assert "(?P<page>" in toc.entry_pattern

    def test_toc_detection_defaults(self):
        """Defaults: style empty (opt-in), entry pattern populated."""
        toc = TocDetection()
        assert toc.style_pattern == ""
        assert toc.entry_pattern.startswith("^(?P<num>")

    def test_round_trip_revhist_label_pattern(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        assert loaded.revision_history_label_pattern == r"(?i)^\s*custom\s+history\s*$"

    def test_revhist_legacy_field_name_migrates(self, tmp_path: Path):
        """Profiles saved with the old ``revision_history_heading_pattern``
        field name still load — the value is migrated into the renamed
        ``revision_history_label_pattern``."""
        legacy_json = tmp_path / "legacy.json"
        legacy_json.write_text(
            '{"revision_history_heading_pattern": "(?i)^legacy$"}',
            encoding="utf-8",
        )
        loaded = DocumentProfile.load_json(legacy_json)
        assert loaded.revision_history_label_pattern == "(?i)^legacy$"

    def test_round_trip_definitions_table_columns(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        assert loaded.definitions_table_term_column == r"(?i)^acronym$"
        assert loaded.definitions_table_definition_column == r"(?i)^expansion$"

    def test_definitions_table_column_defaults(self):
        """Defaults match common acronym/term and definition column shapes."""
        profile = DocumentProfile()
        assert "acronym" in profile.definitions_table_term_column
        assert "term" in profile.definitions_table_term_column
        assert "definition" in profile.definitions_table_definition_column

    def test_embed_glossary_default_true(self):
        """``embed_glossary`` defaults to True — preserves OA behavior
        (glossary section + per-acronym chunks emitted)."""
        assert DocumentProfile().embed_glossary is True

    def test_embed_glossary_round_trip(self, tmp_path: Path):
        original = DocumentProfile(embed_glossary=False)
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)
        assert loaded.embed_glossary is False

    def test_round_trip_plan_metadata(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        pm = loaded.plan_metadata
        assert pm.plan_name.sample_value == "Test_Plan"
        assert pm.plan_id.pattern == r"Plan\s+Id:\s*(\w+)"
        assert pm.version.sample_value == "5"
        # Empty MetadataField should round-trip cleanly
        assert pm.release_date.pattern == ""
        assert pm.release_date.sample_value == ""

    def test_round_trip_document_zones(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        assert len(loaded.document_zones) == 2
        assert loaded.document_zones[0].zone_type == "introduction"
        assert loaded.document_zones[1].heading_text == "SOFTWARE"

    def test_round_trip_header_footer(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        hf = loaded.header_footer
        assert hf.header_patterns == ["Test Header #"]
        assert hf.footer_patterns == []
        assert hf.page_number_pattern == r"^\s*Page\s+\d+\s+of\s+\d+\s*$"

    def test_round_trip_cross_references(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        cr = loaded.cross_reference_patterns
        assert len(cr.standards_citations) == 1
        assert cr.internal_section_refs == r"[Ss]ee\s+[Ss]ection\s+[\d.]+"
        assert cr.requirement_id_refs == r"VZ_REQ_[A-Z0-9_]+_\d+"

    def test_round_trip_body_text(self, tmp_path: Path):
        original = _make_profile()
        json_path = tmp_path / "profile.json"
        original.save_json(json_path)
        loaded = DocumentProfile.load_json(json_path)

        bt = loaded.body_text
        assert bt.font_size_min == 11.5
        assert bt.font_size_max == 12.5
        assert bt.font_families == ["Fanwood"]

    def test_load_existing_profile(self):
        """Load the actual generated VZW profile to ensure compatibility."""
        profile_path = Path("profiles/vzw_oa_profile.json")
        if not profile_path.exists():
            pytest.skip("VZW profile not generated yet")

        profile = DocumentProfile.load_json(profile_path)
        assert profile.profile_name == "VZW_OA"
        assert len(profile.heading_detection.levels) > 0
        assert profile.requirement_id.pattern != ""


def test_from_dict_toc_threshold_default_matches_dataclass():
    """A profile JSON without the key gets the same default as a fresh profile."""
    from core.src.profiler.profile_schema import DocumentProfile

    assert DocumentProfile._from_dict({}).toc_page_threshold == DocumentProfile().toc_page_threshold == 0.7
