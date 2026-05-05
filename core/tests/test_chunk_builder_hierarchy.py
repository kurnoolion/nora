"""Tests for hierarchy path prefixing in chunk text and metadata.

ChunkBuilder prepends the plan_name (document name) as the root of
the [Path: ...] line so the embedding captures the full
Document > Section > Subsection chain, not just the within-document
section path. The full path is also stored in chunk metadata so
retrieval-side grouping can read it without parsing chunk text.
"""

from __future__ import annotations

import pytest

from core.src.vectorstore.chunk_builder import ChunkBuilder
from core.src.vectorstore.config import VectorStoreConfig


def _config(**overrides) -> VectorStoreConfig:
    base = dict(
        include_mno_header=False,
        include_hierarchy_path=True,
        include_req_id=False,
        include_tables=False,
        include_image_context=False,
        include_children_titles=False,
    )
    base.update(overrides)
    return VectorStoreConfig(**base)


def _tree(plan_name: str, plan_id: str, reqs: list[dict]) -> dict:
    return {
        "mno": "VZW",
        "release": "OA-test",
        "plan_id": plan_id,
        "plan_name": plan_name,
        "version": "1",
        "requirements": reqs,
        "definitions_map": {},
        "definitions_section_number": "",
    }


def _req(req_id: str, hierarchy_path: list[str], text: str = "body") -> dict:
    return {
        "req_id": req_id,
        "title": "",
        "text": text,
        "section_number": "4.1",
        "parent_req_id": "",
        "parent_section": "",
        "hierarchy_path": hierarchy_path,
        "zone_type": "",
        "priority": "",
        "applicability": [],
        "tables": [],
        "images": [],
        "children": [],
        "cross_references": {},
    }


class TestHierarchyPathInChunkText:
    def test_plan_name_prepended_to_path(self):
        builder = ChunkBuilder(_config())
        tree = _tree("LTE OTA DM", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS", "ATTACH"]),
        ])
        chunks = builder.build_chunks([tree])
        assert len(chunks) == 1
        assert "[Path: LTE OTA DM > SCENARIOS > ATTACH]" in chunks[0].text

    def test_plan_id_used_when_plan_name_empty(self):
        builder = ChunkBuilder(_config())
        tree = _tree("", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS"]),
        ])
        chunks = builder.build_chunks([tree])
        assert "[Path: LTEOTADM > SCENARIOS]" in chunks[0].text

    def test_path_omitted_when_flag_off(self):
        builder = ChunkBuilder(_config(include_hierarchy_path=False))
        tree = _tree("LTE OTA DM", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS", "ATTACH"]),
        ])
        chunks = builder.build_chunks([tree])
        assert "[Path:" not in chunks[0].text

    def test_empty_hierarchy_list_still_shows_doc_root(self):
        builder = ChunkBuilder(_config())
        tree = _tree("LTE OTA DM", "LTEOTADM", [
            _req("REQ_1", []),
        ])
        chunks = builder.build_chunks([tree])
        assert "[Path: LTE OTA DM]" in chunks[0].text

    def test_no_path_when_both_names_empty_and_hierarchy_empty(self):
        builder = ChunkBuilder(_config())
        tree = _tree("", "", [
            _req("REQ_1", []),
        ])
        chunks = builder.build_chunks([tree])
        assert "[Path:" not in chunks[0].text


class TestHierarchyPathInMetadata:
    def test_full_path_in_metadata(self):
        builder = ChunkBuilder(_config())
        tree = _tree("LTE OTA DM", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS", "ATTACH"]),
        ])
        chunks = builder.build_chunks([tree])
        assert chunks[0].metadata["hierarchy_path"] == [
            "LTE OTA DM", "SCENARIOS", "ATTACH"
        ]

    def test_metadata_path_uses_plan_id_fallback(self):
        builder = ChunkBuilder(_config())
        tree = _tree("", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS"]),
        ])
        chunks = builder.build_chunks([tree])
        assert chunks[0].metadata["hierarchy_path"] == ["LTEOTADM", "SCENARIOS"]

    def test_metadata_path_present_even_when_flag_off(self):
        """hierarchy_path in metadata is independent of include_hierarchy_path."""
        builder = ChunkBuilder(_config(include_hierarchy_path=False))
        tree = _tree("LTE OTA DM", "LTEOTADM", [
            _req("REQ_1", ["SCENARIOS"]),
        ])
        chunks = builder.build_chunks([tree])
        assert chunks[0].metadata["hierarchy_path"] == ["LTE OTA DM", "SCENARIOS"]

    def test_two_reqs_have_distinct_paths(self):
        builder = ChunkBuilder(_config())
        tree = _tree("OTADM", "OTADM", [
            _req("REQ_A", ["§3", "§3.1"]),
            _req("REQ_B", ["§4", "§4.2"]),
        ])
        chunks = builder.build_chunks([tree])
        by_id = {c.metadata["req_id"]: c for c in chunks}
        assert by_id["REQ_A"].metadata["hierarchy_path"] == ["OTADM", "§3", "§3.1"]
        assert by_id["REQ_B"].metadata["hierarchy_path"] == ["OTADM", "§4", "§4.2"]


class TestGlossaryChunkHierarchyPath:
    def test_glossary_chunk_has_doc_root_in_metadata(self):
        builder = ChunkBuilder(_config())
        tree = _tree("LTE OTA DM", "LTEOTADM", [])
        tree["definitions_map"] = {"IMS": "IP Multimedia Subsystem"}
        chunks = builder.build_chunks([tree])
        glossary = [c for c in chunks if c.metadata.get("doc_type") == "glossary_entry"]
        assert len(glossary) == 1
        assert glossary[0].metadata["hierarchy_path"] == ["LTE OTA DM"]

    def test_glossary_chunk_uses_plan_id_fallback(self):
        builder = ChunkBuilder(_config())
        tree = _tree("", "LTEOTADM", [])
        tree["definitions_map"] = {"VoLTE": "Voice over LTE"}
        chunks = builder.build_chunks([tree])
        glossary = [c for c in chunks if c.metadata.get("doc_type") == "glossary_entry"]
        assert glossary[0].metadata["hierarchy_path"] == ["LTEOTADM"]
