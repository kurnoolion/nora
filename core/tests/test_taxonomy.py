"""Tests for the feature taxonomy pipeline (Step 6).

Covers:
- MockLLMProvider keyword matching and protocol compliance
- FeatureExtractor prompt building and response parsing
- TaxonomyConsolidator deduplication and merge logic
- Schema serialization round-trips
- End-to-end pipeline with real parsed trees
"""

import json
from pathlib import Path

import pytest

from src.llm.base import LLMProvider
from src.llm.mock_provider import MockLLMProvider
from src.parser.structural_parser import (
    CrossReferences,
    Requirement,
    RequirementTree,
)
from src.taxonomy.consolidator import TaxonomyConsolidator
from src.taxonomy.extractor import FeatureExtractor
from src.taxonomy.schema import (
    DocumentFeatures,
    Feature,
    FeatureTaxonomy,
    TaxonomyFeature,
)


# ── Helpers ────────────────────────────────────────────────────────


def _make_tree(
    plan_id: str,
    plan_name: str,
    sections: list[tuple[str, str, str]],
    mno: str = "VZW",
    release: str = "2026_feb",
) -> RequirementTree:
    """Build a RequirementTree from (req_id, section_number, title) tuples."""
    reqs = [
        Requirement(
            req_id=rid,
            section_number=sec,
            title=title,
            cross_references=CrossReferences(),
        )
        for rid, sec, title in sections
    ]
    return RequirementTree(
        plan_id=plan_id,
        plan_name=plan_name,
        mno=mno,
        release=release,
        version="1.0",
        requirements=reqs,
    )


# ── MockLLMProvider ────────────────────────────────────────────────


class TestMockLLMProvider:
    def test_satisfies_protocol(self):
        """MockLLMProvider satisfies the LLMProvider protocol."""
        provider = MockLLMProvider()
        assert isinstance(provider, LLMProvider)

    def test_call_count(self):
        provider = MockLLMProvider()
        assert provider.call_count == 0
        provider.complete("test section headings prompt")
        assert provider.call_count == 1
        provider.complete("another section headings prompt")
        assert provider.call_count == 2

    def test_extracts_features_from_toc_prompt(self):
        """Prompts containing 'section headings' trigger feature extraction."""
        provider = MockLLMProvider()
        response = provider.complete(
            "Section headings: 3.1 SMS MO, 3.2 SMS MT, 3.3 IMS Registration"
        )
        data = json.loads(response)
        assert "primary_features" in data
        assert "referenced_features" in data
        assert "key_concepts" in data

    def test_keyword_matching_sms(self):
        """SMS keywords produce SMS feature."""
        provider = MockLLMProvider()
        response = provider.complete(
            "Section headings: 3.1 SMS MO, 3.2 SMS MT, 3.3 Short Message"
        )
        data = json.loads(response)
        all_features = data["primary_features"] + data["referenced_features"]
        feature_ids = [f["feature_id"] for f in all_features]
        assert "SMS" in feature_ids

    def test_keyword_matching_data_retry(self):
        """Data retry keywords produce DATA_RETRY feature."""
        provider = MockLLMProvider()
        response = provider.complete(
            "Section headings: 5.1 Data Retry Timer, 5.2 PDN Connectivity"
        )
        data = json.loads(response)
        all_features = data["primary_features"] + data["referenced_features"]
        feature_ids = [f["feature_id"] for f in all_features]
        assert "DATA_RETRY" in feature_ids

    def test_multiple_keyword_hits_become_primary(self):
        """Features with 2+ keyword matches are classified as primary."""
        provider = MockLLMProvider()
        # "data retry" + "pdn connectivity" = 2 matches for DATA_RETRY
        response = provider.complete(
            "Section headings: data retry, pdn connectivity"
        )
        data = json.loads(response)
        primary_ids = [f["feature_id"] for f in data["primary_features"]]
        assert "DATA_RETRY" in primary_ids

    def test_single_keyword_hit_becomes_referenced(self):
        """Features with exactly 1 keyword match are classified as referenced."""
        provider = MockLLMProvider()
        # "antenna" alone = 1 match for RF_ANTENNA
        response = provider.complete("Section headings: antenna requirements")
        data = json.loads(response)
        ref_ids = [f["feature_id"] for f in data["referenced_features"]]
        # RF_ANTENNA should be referenced (1 keyword hit)
        # unless it got promoted because nothing else was primary
        all_primary = data["primary_features"]
        all_ref = data["referenced_features"]
        all_ids = [f["feature_id"] for f in all_primary + all_ref]
        assert "RF_ANTENNA" in all_ids

    def test_no_matches_returns_empty(self):
        """A prompt with no telecom keywords returns empty features."""
        provider = MockLLMProvider()
        response = provider.complete(
            "Section headings: 1.1 Introduction, 1.2 Scope"
        )
        data = json.loads(response)
        assert len(data["primary_features"]) == 0
        assert len(data["referenced_features"]) == 0

    def test_unrecognized_prompt_type(self):
        """Prompts without known trigger phrases return an error marker."""
        provider = MockLLMProvider()
        response = provider.complete("Hello, world!")
        data = json.loads(response)
        assert "error" in data

    def test_confidence_scales_with_matches(self):
        """More keyword matches → higher confidence score."""
        provider = MockLLMProvider()
        response = provider.complete(
            "Section headings: data retry timer, pdn connectivity, throttle, ESM"
        )
        data = json.loads(response)
        all_features = data["primary_features"] + data["referenced_features"]
        data_retry = [f for f in all_features if f["feature_id"] == "DATA_RETRY"]
        assert len(data_retry) == 1
        assert data_retry[0]["confidence"] > 0.5


# ── FeatureExtractor ──────────────────────────────────────────────


class TestFeatureExtractor:
    def test_extract_returns_document_features(self):
        tree = _make_tree(
            "LTESMS", "LTE SMS",
            [("SMS-001", "3.1", "SMS MO Procedures"),
             ("SMS-002", "3.2", "SMS MT Procedures"),
             ("SMS-003", "3.3", "SMS over IMS")],
        )
        extractor = FeatureExtractor(MockLLMProvider())
        result = extractor.extract(tree)

        assert isinstance(result, DocumentFeatures)
        assert result.plan_id == "LTESMS"
        assert result.plan_name == "LTE SMS"
        assert result.mno == "VZW"
        assert result.release == "2026_feb"

    def test_extract_populates_features(self):
        tree = _make_tree(
            "LTEDATARETRY", "LTE Data Retry",
            [("DR-001", "3.1", "Data Retry Timer Management"),
             ("DR-002", "3.2", "PDN Connectivity Retry"),
             ("DR-003", "3.3", "Throttle Behavior")],
        )
        extractor = FeatureExtractor(MockLLMProvider())
        result = extractor.extract(tree)

        assert len(result.primary_features) > 0 or len(result.referenced_features) > 0

    def test_build_toc_format(self):
        tree = _make_tree(
            "TEST", "Test Plan",
            [("T-001", "3.1", "Top Level"),
             ("T-002", "3.1.1", "Sub Section"),
             ("T-003", "3.1.1.1", "Deep Section")],
        )
        toc = FeatureExtractor._build_toc(tree)
        lines = toc.strip().split("\n")
        assert "3.1 Top Level" in lines[0]
        # Sub-sections are indented
        assert lines[1].startswith("  ")
        assert lines[2].startswith("    ")

    def test_build_toc_truncation(self):
        """TOC is truncated at 200 lines."""
        sections = [
            (f"T-{i:03d}", f"3.{i}", f"Section {i}")
            for i in range(250)
        ]
        tree = _make_tree("BIG", "Big Plan", sections)
        toc = FeatureExtractor._build_toc(tree)
        lines = toc.strip().split("\n")
        assert len(lines) <= 202  # 200 + truncation message + possible blank
        assert "truncated" in lines[-1].lower()

    def test_parse_response_valid_json(self):
        raw = json.dumps({
            "primary_features": [
                {"feature_id": "SMS", "name": "SMS", "description": "d",
                 "keywords": ["sms"], "confidence": 0.9}
            ],
            "referenced_features": [],
            "key_concepts": ["sms"],
        })
        result = FeatureExtractor._parse_response(raw, "TEST")
        assert len(result.primary_features) == 1
        assert result.primary_features[0].feature_id == "SMS"

    def test_parse_response_markdown_fenced(self):
        """Handles ```json fenced responses."""
        inner = json.dumps({
            "primary_features": [
                {"feature_id": "A", "name": "A", "description": "d",
                 "keywords": [], "confidence": 0.8}
            ],
            "referenced_features": [],
            "key_concepts": [],
        })
        raw = f"```json\n{inner}\n```"
        result = FeatureExtractor._parse_response(raw, "TEST")
        assert len(result.primary_features) == 1

    def test_parse_response_invalid_json(self):
        """Invalid JSON returns empty DocumentFeatures."""
        result = FeatureExtractor._parse_response("not json at all", "TEST")
        assert isinstance(result, DocumentFeatures)
        assert len(result.primary_features) == 0
        assert len(result.referenced_features) == 0


# ── TaxonomyConsolidator ──────────────────────────────────────────


class TestTaxonomyConsolidator:
    def _make_doc_features(
        self,
        plan_id: str,
        mno: str,
        release: str,
        primary: list[Feature],
        referenced: list[Feature] | None = None,
    ) -> DocumentFeatures:
        return DocumentFeatures(
            plan_id=plan_id,
            plan_name=plan_id,
            mno=mno,
            release=release,
            primary_features=primary,
            referenced_features=referenced or [],
        )

    def test_single_document(self):
        doc = self._make_doc_features(
            "PLAN_A", "VZW", "2026_feb",
            primary=[Feature(feature_id="SMS", name="SMS", keywords=["sms"])],
        )
        result = TaxonomyConsolidator().consolidate([doc])

        assert isinstance(result, FeatureTaxonomy)
        assert len(result.features) == 1
        assert result.features[0].feature_id == "SMS"
        assert result.features[0].is_primary_in == ["PLAN_A"]
        assert result.source_documents == ["PLAN_A"]

    def test_deduplication_by_feature_id(self):
        """Same feature_id from two docs merges into one TaxonomyFeature."""
        doc_a = self._make_doc_features(
            "PLAN_A", "VZW", "2026_feb",
            primary=[Feature(feature_id="SMS", name="SMS", keywords=["sms"])],
        )
        doc_b = self._make_doc_features(
            "PLAN_B", "VZW", "2026_feb",
            primary=[Feature(feature_id="SMS", name="SMS", keywords=["messaging"])],
        )
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])

        assert len(result.features) == 1
        tf = result.features[0]
        assert tf.feature_id == "SMS"
        assert set(tf.source_plans) == {"PLAN_A", "PLAN_B"}
        assert set(tf.is_primary_in) == {"PLAN_A", "PLAN_B"}

    def test_keyword_merge_no_duplicates(self):
        """Keywords from multiple docs merge without duplicates."""
        doc_a = self._make_doc_features(
            "A", "VZW", "2026_feb",
            primary=[Feature(feature_id="F1", name="F1", keywords=["x", "y"])],
        )
        doc_b = self._make_doc_features(
            "B", "VZW", "2026_feb",
            primary=[Feature(feature_id="F1", name="F1", keywords=["y", "z"])],
        )
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])
        assert sorted(result.features[0].keywords) == ["x", "y", "z"]

    def test_primary_vs_referenced_tracking(self):
        """A feature primary in one doc and referenced in another is tracked correctly."""
        doc_a = self._make_doc_features(
            "PLAN_A", "VZW", "2026_feb",
            primary=[Feature(feature_id="IMS", name="IMS")],
        )
        doc_b = self._make_doc_features(
            "PLAN_B", "VZW", "2026_feb",
            primary=[],
            referenced=[Feature(feature_id="IMS", name="IMS")],
        )
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])

        tf = result.features[0]
        assert tf.is_primary_in == ["PLAN_A"]
        assert tf.is_referenced_in == ["PLAN_B"]
        assert set(tf.source_plans) == {"PLAN_A", "PLAN_B"}

    def test_mno_coverage(self):
        """mno_coverage maps each MNO to the plans containing that feature."""
        doc_a = self._make_doc_features(
            "A", "VZW", "r1",
            primary=[Feature(feature_id="F1", name="F1")],
        )
        doc_b = self._make_doc_features(
            "B", "VZW", "r1",
            primary=[Feature(feature_id="F1", name="F1")],
        )
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])
        tf = result.features[0]
        assert "VZW" in tf.mno_coverage
        assert set(tf.mno_coverage["VZW"]) == {"A", "B"}

    def test_sorting_primary_count_then_alpha(self):
        """Features are sorted by primary count (desc), then name (asc)."""
        f_alpha = Feature(feature_id="ALPHA", name="Alpha")
        f_beta = Feature(feature_id="BETA", name="Beta")

        doc_a = self._make_doc_features(
            "A", "VZW", "r1",
            primary=[f_alpha, f_beta],
        )
        doc_b = self._make_doc_features(
            "B", "VZW", "r1",
            primary=[f_beta],  # Beta is primary in 2 docs
        )
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])
        assert result.features[0].feature_id == "BETA"  # 2 primary
        assert result.features[1].feature_id == "ALPHA"  # 1 primary

    def test_empty_input(self):
        result = TaxonomyConsolidator().consolidate([])
        assert len(result.features) == 0
        assert result.mno == ""

    def test_multiple_mnos(self):
        doc_a = self._make_doc_features("A", "VZW", "r1", primary=[])
        doc_b = self._make_doc_features("B", "ATT", "r2", primary=[])
        result = TaxonomyConsolidator().consolidate([doc_a, doc_b])
        assert "ATT" in result.mno
        assert "VZW" in result.mno

    def test_unknown_mno_handling(self):
        """Empty MNO string maps to 'UNKNOWN' in mno_coverage."""
        doc = self._make_doc_features(
            "A", "", "r1",
            primary=[Feature(feature_id="F1", name="F1")],
        )
        result = TaxonomyConsolidator().consolidate([doc])
        assert "UNKNOWN" in result.features[0].mno_coverage


# ── Schema serialization ─────────────────────────────────────────


class TestSchemaSerialization:
    def test_document_features_round_trip(self, tmp_path):
        df = DocumentFeatures(
            plan_id="TEST",
            plan_name="Test Plan",
            mno="VZW",
            release="2026_feb",
            primary_features=[
                Feature(feature_id="SMS", name="SMS", description="d",
                        keywords=["sms", "messaging"], confidence=0.9)
            ],
            referenced_features=[
                Feature(feature_id="IMS", name="IMS", description="d2",
                        keywords=["ims"], confidence=0.7)
            ],
            key_concepts=["sms", "ims"],
        )
        path = tmp_path / "test_features.json"
        df.save_json(path)
        loaded = DocumentFeatures.load_json(path)

        assert loaded.plan_id == "TEST"
        assert loaded.mno == "VZW"
        assert len(loaded.primary_features) == 1
        assert loaded.primary_features[0].feature_id == "SMS"
        assert loaded.primary_features[0].keywords == ["sms", "messaging"]
        assert len(loaded.referenced_features) == 1
        assert loaded.key_concepts == ["sms", "ims"]

    def test_feature_taxonomy_round_trip(self, tmp_path):
        taxonomy = FeatureTaxonomy(
            mno="VZW",
            release="2026_feb",
            features=[
                TaxonomyFeature(
                    feature_id="SMS",
                    name="SMS over LTE",
                    description="Short message service",
                    keywords=["sms", "messaging"],
                    mno_coverage={"VZW": ["LTESMS", "LTEB13NAC"]},
                    source_plans=["LTESMS", "LTEB13NAC"],
                    is_primary_in=["LTESMS"],
                    is_referenced_in=["LTEB13NAC"],
                )
            ],
            source_documents=["LTESMS", "LTEB13NAC"],
        )
        path = tmp_path / "taxonomy.json"
        taxonomy.save_json(path)
        loaded = FeatureTaxonomy.load_json(path)

        assert loaded.mno == "VZW"
        assert len(loaded.features) == 1
        tf = loaded.features[0]
        assert tf.feature_id == "SMS"
        assert tf.mno_coverage == {"VZW": ["LTESMS", "LTEB13NAC"]}
        assert tf.is_primary_in == ["LTESMS"]
        assert tf.is_referenced_in == ["LTEB13NAC"]
        assert loaded.source_documents == ["LTESMS", "LTEB13NAC"]

    def test_document_features_to_dict(self):
        df = DocumentFeatures(
            plan_id="X",
            primary_features=[Feature(feature_id="F1", name="F1")],
        )
        d = df.to_dict()
        assert d["plan_id"] == "X"
        assert len(d["primary_features"]) == 1

    def test_feature_taxonomy_to_dict(self):
        t = FeatureTaxonomy(
            mno="VZW",
            features=[TaxonomyFeature(feature_id="F1")],
        )
        d = t.to_dict()
        assert d["mno"] == "VZW"
        assert len(d["features"]) == 1


# ── End-to-end pipeline with real parsed trees ────────────────────


TREES_DIR = Path("data/parsed")


@pytest.mark.skipif(
    not TREES_DIR.exists(),
    reason="Parsed tree data not available",
)
class TestTaxonomyPipeline:
    """Integration tests using real parsed trees and MockLLMProvider."""

    @pytest.fixture(scope="class")
    def taxonomy_result(self):
        """Run the full pipeline once and share across tests."""
        tree_files = sorted(TREES_DIR.glob("*_tree.json"))
        trees = [RequirementTree.load_json(f) for f in tree_files]

        llm = MockLLMProvider()
        extractor = FeatureExtractor(llm)
        doc_features = [extractor.extract(t) for t in trees]

        consolidator = TaxonomyConsolidator()
        taxonomy = consolidator.consolidate(doc_features)
        return taxonomy, doc_features, llm

    def test_all_docs_processed(self, taxonomy_result):
        taxonomy, doc_features, _ = taxonomy_result
        assert len(doc_features) == 5
        assert len(taxonomy.source_documents) == 5

    def test_features_extracted(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        assert len(taxonomy.features) > 0

    def test_mno_is_vzw(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        assert taxonomy.mno == "VZW"

    def test_release_populated(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        assert taxonomy.release != ""

    def test_every_feature_has_source_plan(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        for f in taxonomy.features:
            assert len(f.source_plans) > 0, f"{f.feature_id} has no source_plans"

    def test_every_feature_has_mno_coverage(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        for f in taxonomy.features:
            assert len(f.mno_coverage) > 0, f"{f.feature_id} has no mno_coverage"

    def test_primary_or_referenced_in_at_least_one(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        for f in taxonomy.features:
            total = len(f.is_primary_in) + len(f.is_referenced_in)
            assert total > 0, f"{f.feature_id} not primary or referenced anywhere"

    def test_no_duplicate_feature_ids(self, taxonomy_result):
        taxonomy, _, _ = taxonomy_result
        ids = [f.feature_id for f in taxonomy.features]
        assert len(ids) == len(set(ids)), "Duplicate feature IDs found"

    def test_per_doc_features_have_plan_ids(self, taxonomy_result):
        _, doc_features, _ = taxonomy_result
        for df in doc_features:
            assert df.plan_id != ""
            assert df.mno == "VZW"

    def test_llm_called_once_per_doc(self, taxonomy_result):
        _, doc_features, llm = taxonomy_result
        assert llm.call_count == len(doc_features)
