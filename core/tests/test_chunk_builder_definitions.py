"""Tests for FR-35 [D-032] inline definition expansion in chunk_builder.

The parser populates `RequirementTree.definitions_map` from the document's
glossary; the chunk builder reads it from the tree dict and expands the
first occurrence of each known term inline within every chunk's text
before embedding. Expansion is per-document, idempotent, and skips
chunks belonging to the definitions section itself.
"""

from __future__ import annotations

from core.src.vectorstore.chunk_builder import ChunkBuilder
from core.src.vectorstore.config import VectorStoreConfig


def _config() -> VectorStoreConfig:
    return VectorStoreConfig(
        include_mno_header=False,
        include_hierarchy_path=False,
        include_req_id=False,
    )


def _tree(reqs: list[dict], definitions_map: dict[str, str], defs_section_num: str = "") -> dict:
    return {
        "mno": "VZW",
        "release": "OA-test",
        "plan_id": "TESTPLAN",
        "plan_name": "Test Plan",
        "version": "1",
        "requirements": reqs,
        "definitions_map": definitions_map,
        "definitions_section_number": defs_section_num,
    }


# ---------------------------------------------------------------------------
# Basic expansion
# ---------------------------------------------------------------------------


def test_first_occurrence_expanded_inline():
    """`ETWS` in chunk text → `ETWS (Earthquake and Tsunami Warning System)`."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "ETWS Behavior",
            "text": "The device shall support ETWS as defined.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {"ETWS": "Earthquake and Tsunami Warning System"})
    chunks = builder.build_chunks([tree])
    assert len(chunks) == 1
    text = chunks[0].text
    assert "ETWS (Earthquake and Tsunami Warning System)" in text


def test_expansion_only_once_per_chunk():
    """Subsequent occurrences within the same chunk are NOT expanded."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "ETWS Behavior",
            "text": "ETWS shall be supported. ETWS protocol applies. ETWS is mandatory.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {"ETWS": "Earthquake and Tsunami Warning System"})
    chunks = builder.build_chunks([tree])
    text = chunks[0].text
    # First ETWS expanded; subsequent kept as bare term.
    assert text.count("ETWS (Earthquake") == 1
    # Total ETWS occurrences (including one inside the parenthetical's own
    # leading "ETWS"): >= 3 (title, body x3) — confirms not all were expanded.
    assert text.count("ETWS") >= 4  # title + 3 body + 0 inside expansion


def test_multiple_terms_all_expanded():
    """Every term in the map gets one expansion, longer terms first."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "Procedures",
            "text": "Use SUPL for location and ETWS for alerts.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {
        "SUPL": "Secure User Plane Location",
        "ETWS": "Earthquake and Tsunami Warning System",
    })
    text = builder.build_chunks([tree])[0].text
    assert "SUPL (Secure User Plane Location)" in text
    assert "ETWS (Earthquake and Tsunami Warning System)" in text


def test_longer_term_matches_before_shorter():
    """Sort by length descending — `IMS REGISTRATION` matches before `IMS`
    so the multi-word term wins when both are defined."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "Reg",
            "text": "IMS REGISTRATION is required for VoLTE.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {
        "IMS": "IP Multimedia Subsystem",
        "IMS REGISTRATION": "Registration with the IP Multimedia Subsystem",
    })
    text = builder.build_chunks([tree])[0].text
    # The multi-word term should win — its expansion is present, and the
    # bare "IMS" expansion shouldn't fire (one expansion per term).
    assert "IMS REGISTRATION (Registration with the IP Multimedia Subsystem)" in text


def test_no_expansion_when_definitions_map_empty():
    """Empty map → no-op; chunk text passes through unchanged."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "No Defs",
            "text": "The device shall support ETWS.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {})
    text = builder.build_chunks([tree])[0].text
    assert "(Earthquake" not in text


def test_word_boundary_prevents_false_match():
    """`RAT` should NOT match inside `RATIO` or `CRATE`."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "Throughput",
            "text": "The RATIO is 5:1. Use the appropriate RAT.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {"RAT": "Radio Access Technology"})
    text = builder.build_chunks([tree])[0].text
    # RAT (whole word) expanded once; RATIO untouched.
    assert "RAT (Radio Access Technology)" in text
    assert "RATIO" in text
    assert "RATIO (Radio" not in text


# ---------------------------------------------------------------------------
# Definitions-section self-skip
# ---------------------------------------------------------------------------


def test_definitions_section_chunk_not_self_expanded():
    """Chunks belonging to the definitions section don't get expanded
    (avoid `ETWS (Earthquake...) — Earthquake...` double-anchoring)."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            # The definitions section itself.
            "req_id": "REQ_DEFS",
            "section_number": "1.1",
            "title": "Acronyms",
            "text": "ETWS - Earthquake and Tsunami Warning System",
            "tables": [],
            "images": [],
        },
        {
            # A regular requirement that uses the term.
            "req_id": "REQ_OTHER",
            "section_number": "2.1",
            "title": "Behavior",
            "text": "ETWS shall be supported.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(
        reqs,
        {"ETWS": "Earthquake and Tsunami Warning System"},
        defs_section_num="1.1",
    )
    chunks = {c.chunk_id: c for c in builder.build_chunks([tree])}

    defs_chunk = chunks["req:REQ_DEFS"]
    other_chunk = chunks["req:REQ_OTHER"]

    # The definitions chunk preserves its original entry (no
    # parenthetical expansion injected).
    assert "ETWS - Earthquake and Tsunami Warning System" in defs_chunk.text
    assert "ETWS (Earthquake and Tsunami Warning System)" not in defs_chunk.text

    # The other chunk DOES get expanded.
    assert "ETWS (Earthquake and Tsunami Warning System)" in other_chunk.text


def test_definitions_descendant_section_also_skipped():
    """Sub-sections under the definitions section (e.g. 1.1.1) also skip
    expansion — they're typically per-term entries with the same risk."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_SUB",
            "section_number": "1.1.1",  # descendant of "1.1"
            "title": "ETWS",
            "text": "ETWS - Earthquake and Tsunami Warning System (full text).",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(
        reqs,
        {"ETWS": "Earthquake and Tsunami Warning System"},
        defs_section_num="1.1",
    )
    chunks = builder.build_chunks([tree])
    assert "ETWS (Earthquake and Tsunami Warning System)" not in chunks[0].text


def test_table_anchored_under_definitions_skipped():
    """Table-anchored req with parent_section under the definitions
    section is also skipped (uses parent_section, not section_number)."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_TBL",
            "section_number": "",        # table-anchored
            "parent_section": "1.1",     # descendant of definitions
            "title": "",
            "text": "ETWS row",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(
        reqs,
        {"ETWS": "Earthquake and Tsunami Warning System"},
        defs_section_num="1.1",
    )
    chunks = builder.build_chunks([tree])
    assert "ETWS (Earthquake" not in chunks[0].text
