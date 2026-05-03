"""Tests for parent-chunk augmentation with child titles.

The chunk builder appends a `[Subsections: t1; t2; ...]` line to a
parent chunk's text when `config.include_children_titles` is True
and the requirement has children. The augmented text gets embedded
+ BM25-indexed alongside the parent's own content, lifting thin
parent/overview chunks for breadth queries.
"""

from __future__ import annotations

from core.src.vectorstore.chunk_builder import ChunkBuilder
from core.src.vectorstore.config import VectorStoreConfig


def _config(**overrides) -> VectorStoreConfig:
    """Test fixture forces `include_children_titles=True` (the production
    default is False — augmentation is opt-in). Other content fields are
    suppressed so assertions can pin substring presence cleanly."""
    base = dict(
        include_mno_header=False,
        include_hierarchy_path=False,
        include_req_id=False,
        include_tables=False,
        include_image_context=False,
        include_children_titles=True,
    )
    base.update(overrides)
    return VectorStoreConfig(**base)


def _tree(reqs: list[dict]) -> dict:
    return {
        "mno": "VZW",
        "release": "OA-test",
        "plan_id": "TESTPLAN",
        "plan_name": "Test Plan",
        "version": "1",
        "requirements": reqs,
        "definitions_map": {},
        "definitions_section_number": "",
    }


def _parent_with_children() -> list[dict]:
    """Three-req tree: parent + 2 children. Parent's own body is
    intentionally thin (heading-only)."""
    return [
        {
            "req_id": "REQ_PARENT",
            "section_number": "1.1",
            "title": "SMS over IMS - overview",
            "text": "",   # heading-only parent
            "tables": [],
            "images": [],
            "children": ["REQ_CHILD_A", "REQ_CHILD_B"],
        },
        {
            "req_id": "REQ_CHILD_A",
            "section_number": "1.1.1",
            "title": "MO SMS",
            "text": "Mobile-originated SMS body content.",
            "tables": [],
            "images": [],
            "children": [],
        },
        {
            "req_id": "REQ_CHILD_B",
            "section_number": "1.1.2",
            "title": "MT SMS",
            "text": "Mobile-terminated SMS body content.",
            "tables": [],
            "images": [],
            "children": [],
        },
    ]


def test_parent_chunk_lists_immediate_children():
    """The parent's chunk text gains a `[Subsections: ...]` line
    listing every immediate child's title (semicolon-separated)."""
    builder = ChunkBuilder(_config())
    chunks = builder.build_chunks([_tree(_parent_with_children())])
    parent = next(c for c in chunks if c.chunk_id == "req:REQ_PARENT")
    assert "[Subsections: MO SMS; MT SMS]" in parent.text


def test_leaf_chunk_has_no_subsections_line():
    """Children with no further descendants don't get an empty
    `[Subsections: ]` line — the section is omitted entirely."""
    builder = ChunkBuilder(_config())
    chunks = builder.build_chunks([_tree(_parent_with_children())])
    leaf = next(c for c in chunks if c.chunk_id == "req:REQ_CHILD_A")
    assert "Subsections" not in leaf.text


def test_disabled_via_config():
    """`include_children_titles=False` preserves the legacy
    behavior — no `[Subsections: ...]` line is emitted even when
    the parent has children."""
    builder = ChunkBuilder(_config(include_children_titles=False))
    chunks = builder.build_chunks([_tree(_parent_with_children())])
    parent = next(c for c in chunks if c.chunk_id == "req:REQ_PARENT")
    assert "Subsections" not in parent.text


def test_children_capped_with_overflow_marker():
    """`max_children_titles=N` caps the visible list and appends a
    `(+M more)` marker so the truncation is explicit."""
    # Parent with 5 children, cap = 2
    reqs = [
        {
            "req_id": "P",
            "section_number": "1",
            "title": "Parent",
            "text": "",
            "tables": [],
            "images": [],
            "children": ["C1", "C2", "C3", "C4", "C5"],
        },
    ]
    for i in range(1, 6):
        reqs.append({
            "req_id": f"C{i}",
            "section_number": f"1.{i}",
            "title": f"Child{i}",
            "text": "",
            "tables": [],
            "images": [],
            "children": [],
        })
    builder = ChunkBuilder(_config(max_children_titles=2))
    chunks = builder.build_chunks([_tree(reqs)])
    parent = next(c for c in chunks if c.chunk_id == "req:P")
    assert "[Subsections: Child1; Child2; (+3 more)]" in parent.text


def test_unresolved_child_id_is_skipped():
    """Defensive: a child reference to a missing req_id is silently
    skipped (e.g., struck/cascaded child whose req was dropped from
    the tree but the parent's children list wasn't fixed up)."""
    reqs = [
        {
            "req_id": "P",
            "section_number": "1",
            "title": "Parent",
            "text": "",
            "tables": [],
            "images": [],
            "children": ["C_LIVE", "C_MISSING"],
        },
        {
            "req_id": "C_LIVE",
            "section_number": "1.1",
            "title": "Live Child",
            "text": "",
            "tables": [],
            "images": [],
            "children": [],
        },
        # C_MISSING intentionally absent from the tree
    ]
    builder = ChunkBuilder(_config())
    chunks = builder.build_chunks([_tree(reqs)])
    parent = next(c for c in chunks if c.chunk_id == "req:P")
    assert "[Subsections: Live Child]" in parent.text
    assert "C_MISSING" not in parent.text


def test_zero_cap_disables_emission():
    """`max_children_titles=0` means "list nothing" — the line is
    suppressed entirely (matches `include_children_titles=False`
    semantics for users who want to keep the flag on but
    temporarily zero the cap)."""
    builder = ChunkBuilder(_config(max_children_titles=0))
    chunks = builder.build_chunks([_tree(_parent_with_children())])
    parent = next(c for c in chunks if c.chunk_id == "req:REQ_PARENT")
    # Cap=0 means no children pass the filter → no line emitted
    assert "Subsections" not in parent.text


def test_body_thinness_gate_suppresses_for_substantial_parents():
    """Augmentation is gated on `body length < children_titles_body_
    threshold`. A parent with substantial body content does NOT get
    augmented even when it has children — augmenting rich parents
    displaces their leaves from cross-doc top-k retrieval."""
    long_body = "Paragraph content. " * 30  # ~570 chars
    reqs = [
        {
            "req_id": "P_RICH",
            "section_number": "1",
            "title": "Substantive section",
            "text": long_body,
            "tables": [],
            "images": [],
            "children": ["C1"],
        },
        {
            "req_id": "C1",
            "section_number": "1.1",
            "title": "ChildA",
            "text": "leaf body",
            "tables": [],
            "images": [],
            "children": [],
        },
    ]
    builder = ChunkBuilder(_config(children_titles_body_threshold=300))
    chunks = builder.build_chunks([_tree(reqs)])
    parent = next(c for c in chunks if c.chunk_id == "req:P_RICH")
    # Body is well over 300 chars → augmentation suppressed
    assert "Subsections" not in parent.text


def test_body_thinness_gate_fires_for_overview_parents():
    """Heading-only / brief-intro parents (body < threshold) DO get
    augmented — that's the case the feature exists for."""
    reqs = [
        {
            "req_id": "P_OVERVIEW",
            "section_number": "1",
            "title": "Overview",
            "text": "",  # heading-only
            "tables": [],
            "images": [],
            "children": ["C1"],
        },
        {
            "req_id": "C1",
            "section_number": "1.1",
            "title": "Leaf",
            "text": "leaf body",
            "tables": [],
            "images": [],
            "children": [],
        },
    ]
    builder = ChunkBuilder(_config(children_titles_body_threshold=300))
    chunks = builder.build_chunks([_tree(reqs)])
    parent = next(c for c in chunks if c.chunk_id == "req:P_OVERVIEW")
    assert "[Subsections: Leaf]" in parent.text
