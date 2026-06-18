"""Tests for ancestor-section Context in chunk text (D-DRAFT-5, strand
multi-mno-nora).

In leading_id_body corpora, section/subsection nodes are non-requirement
context living in `tree.requirements` with an empty req_id. When the tree's
`build_context` is `"path_and_content"`, the chunk builder bakes each
requirement's enclosing-section chain (heading + body) into the chunk text via
the shared `build_context_string` helper. `"path"` is intentionally NOT emitted
here — the `[Path: …]` breadcrumb already carries it (decision: suppress the
path block in NORA to avoid duplication).
"""

from __future__ import annotations

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


def _section(section_number: str, title: str, text: str) -> dict:
    """A context section node — empty req_id, carries section_number/title/text."""
    return {
        "req_id": "", "title": title, "text": text,
        "section_number": section_number, "parent_req_id": "",
        "parent_section": "", "hierarchy_path": [], "zone_type": "",
        "priority": "", "applicability": [], "tables": [], "images": [],
        "children": [], "cross_references": {},
    }


def _leading_req(req_id: str, parent_section: str, title: str, text: str) -> dict:
    return {
        "req_id": req_id, "title": title, "text": text,
        "section_number": "", "parent_req_id": "",
        "parent_section": parent_section, "hierarchy_path": [], "zone_type": "",
        "priority": "", "applicability": [], "tables": [], "images": [],
        "children": [], "cross_references": {},
    }


def _tree(build_context: str, nodes: list[dict]) -> dict:
    return {
        "mno": "MNOB", "release": "r1", "plan_id": "PLANX", "plan_name": "",
        "version": "1", "build_context": build_context, "requirements": nodes,
        "detection_mode": "leading_id_body",
        "definitions_map": {}, "definitions_section_number": "",
    }


def _chunk_text(build_context: str) -> str:
    nodes = [
        _section("5", "Bands", "Chapter 5 intro."),
        _section("5.1", "Frequency", "Frequency intro."),
        _section("5.1.2", "LTE", "LTE intro."),
        _leading_req("ABC-PLANX-1", "5.1.2", "Band 13", "Device shall support band 13."),
    ]
    builder = ChunkBuilder(_config())
    chunks = builder.build_chunks([_tree(build_context, nodes)])
    # Only the leading-id req produces a chunk; section nodes have empty req_id.
    assert len(chunks) == 1
    return chunks[0].text


class TestContextInChunkText:
    def test_path_and_content_bakes_section_chain(self):
        text = _chunk_text("path_and_content")
        assert "[5 Bands]" in text and "Chapter 5 intro." in text
        assert "[5.1 Frequency]" in text and "Frequency intro." in text
        assert "[5.1.2 LTE]" in text and "LTE intro." in text
        # ordered top-down, before the requirement body
        assert text.index("[5 Bands]") < text.index("[5.1.2 LTE]")
        assert text.index("[5.1.2 LTE]") < text.index("Device shall support band 13.")

    def test_path_mode_suppressed_in_nora(self):
        # path is carried by [Path: …]; no [Context: …] / [N Title] block emitted.
        text = _chunk_text("path")
        assert "[Context:" not in text
        assert "[5 Bands]" not in text
        assert "Chapter 5 intro." not in text

    def test_none_mode_no_context(self):
        text = _chunk_text("none")
        assert "[Context:" not in text
        assert "[5 Bands]" not in text
