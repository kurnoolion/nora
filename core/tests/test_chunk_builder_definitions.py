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


def _req_chunks(chunks):
    """Filter to per-requirement chunks. Glossary-entry chunks
    (`doc_type=glossary_entry`) are produced alongside (D-043) but
    are tested separately — keep the inline-expansion tests focused
    on requirement chunks."""
    return [c for c in chunks if c.metadata.get("doc_type") == "requirement"]


# ---------------------------------------------------------------------------
# Basic expansion
# ---------------------------------------------------------------------------


def test_glossary_chunks_one_per_acronym():
    """D-043: every entry in `definitions_map` becomes its own short
    chunk. These are the answer surfaces for "What is X?" queries —
    short, acronym-prefixed, easy for both BM25 and dense retrieval
    to rank top when the query is acronym-shaped."""
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
    defs = {
        "ETWS": "Earthquake and Tsunami Warning System",
        "SDM": "Subscriber Device Management",
    }
    tree = _tree(reqs, defs)
    chunks = builder.build_chunks([tree])
    glossary = [c for c in chunks if c.metadata.get("doc_type") == "glossary_entry"]
    by_term = {c.metadata["acronym"]: c for c in glossary}
    assert set(by_term.keys()) == {"ETWS", "SDM"}
    sdm = by_term["SDM"]
    assert sdm.chunk_id == "glossary:TESTPLAN:SDM"
    assert sdm.metadata["expansion"] == "Subscriber Device Management"
    # The chunk text leads with the acronym so dense embedding picks
    # it up on short queries like "What is SDM?".
    assert "SDM:" in sdm.text
    assert "Subscriber Device Management" in sdm.text


def test_glossary_chunks_skipped_when_no_defs():
    """No definitions_map → no glossary chunks (not even an empty one)."""
    builder = ChunkBuilder(_config())
    reqs = [{
        "req_id": "REQ_1", "section_number": "2.1", "title": "x",
        "text": "body", "tables": [], "images": [],
    }]
    tree = _tree(reqs, {})
    chunks = builder.build_chunks([tree])
    assert all(c.metadata.get("doc_type") != "glossary_entry" for c in chunks)


def test_glossary_chunk_id_slugifies_special_chars():
    """Some acronyms have spaces / punctuation (e.g. `IMEI SV`,
    `OMA-DM`). The chunk_id slug must remain filesystem-safe and
    unique without losing readability."""
    builder = ChunkBuilder(_config())
    tree = _tree([], {"OMA-DM": "Open Mobile Alliance Device Management",
                       "IMEI SV": "IMEI Software Version"})
    chunks = builder.build_chunks([tree])
    ids = {c.chunk_id for c in chunks if c.metadata.get("doc_type") == "glossary_entry"}
    assert "glossary:TESTPLAN:OMA-DM" in ids
    assert "glossary:TESTPLAN:IMEI_SV" in ids


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
    chunks = _req_chunks(builder.build_chunks([tree]))
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
    text = _req_chunks(builder.build_chunks([tree]))[0].text
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
    text = _req_chunks(builder.build_chunks([tree]))[0].text
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
    text = _req_chunks(builder.build_chunks([tree]))[0].text
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
    text = _req_chunks(builder.build_chunks([tree]))[0].text
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
    chunks = _req_chunks(builder.build_chunks([tree]))
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
    chunks = _req_chunks(builder.build_chunks([tree]))
    assert "ETWS (Earthquake" not in chunks[0].text


# ---------------------------------------------------------------------------
# Dual-form retrievability — both the acronym AND the expansion must
# survive into the chunk as independently-matchable tokens, so a query
# for either form retrieves the chunk. Pinning the chunker's "keep
# acronym + add bracketed expansion" contract end-to-end through the
# BM25 tokenizer.
# ---------------------------------------------------------------------------


def _expanded_chunk(term: str, expansion: str) -> str:
    """Build a single chunk's text with one acronym known to the
    definitions map and one body sentence that mentions it once."""
    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": f"{term} Behavior",
            "text": f"The device shall support {term} as defined in the spec.",
            "tables": [],
            "images": [],
        },
    ]
    tree = _tree(reqs, {term: expansion})
    chunks = _req_chunks(builder.build_chunks([tree]))
    assert len(chunks) == 1
    return chunks[0].text


def test_expanded_chunk_contains_both_forms():
    """The chunker's contract: keep the acronym AND add the expansion
    in brackets on first occurrence. Both must literally be present in
    the chunk text so dense embeddings see both."""
    text = _expanded_chunk("ETWS", "Earthquake and Tsunami Warning System")
    # Acronym still there
    assert "ETWS" in text
    # Expansion text spans intact (not just substrings — full phrase)
    assert "Earthquake and Tsunami Warning System" in text
    # Bracketed format on first occurrence
    assert "ETWS (Earthquake and Tsunami Warning System)" in text


def test_acronym_query_tokenizes_to_chunk_token():
    """A query for just the acronym ("ETWS support") must yield a
    token that's also in the expanded chunk's token set — so BM25
    can match. Tests against the production BM25 tokenizer."""
    from core.src.query.bm25_index import tokenize

    chunk_text = _expanded_chunk("ETWS", "Earthquake and Tsunami Warning System")
    chunk_tokens = set(tokenize(chunk_text))
    query_tokens = set(tokenize("ETWS support"))

    # The acronym tokenizes to a token both sides share
    assert "etws" in query_tokens
    assert "etws" in chunk_tokens
    # i.e. there's at least one shared term, so BM25 idf > 0
    assert query_tokens & chunk_tokens


def test_expansion_query_tokenizes_to_chunk_tokens():
    """The dual: a query that uses ONLY the expansion (no acronym) must
    still share tokens with the chunk thanks to the bracketed expansion
    landing in the chunk text."""
    from core.src.query.bm25_index import tokenize

    chunk_text = _expanded_chunk("ETWS", "Earthquake and Tsunami Warning System")
    chunk_tokens = set(tokenize(chunk_text))
    # Query uses the expansion phrase, NOT the acronym
    query_tokens = set(tokenize("Earthquake and Tsunami Warning System support"))

    # All content tokens from the expansion are in the chunk
    assert "earthquake" in chunk_tokens
    assert "tsunami" in chunk_tokens
    assert "warning" in chunk_tokens
    assert "system" in chunk_tokens
    # Query and chunk share the expansion tokens (BM25 idf > 0)
    assert "earthquake" in query_tokens
    overlap = query_tokens & chunk_tokens
    # At least the four content tokens must overlap
    assert {"earthquake", "tsunami", "warning", "system"} <= overlap


def test_neither_acronym_nor_expansion_query_matches_when_definitions_map_empty():
    """Sanity: when the definitions map is empty, no expansion happens.
    A chunk that mentions the acronym but NOT the expansion shares only
    the acronym token with an acronym query, and shares NOTHING with an
    expansion-only query. Confirms the dual-form property is provided
    by the expansion, not by accident."""
    from core.src.query.bm25_index import tokenize

    builder = ChunkBuilder(_config())
    reqs = [
        {
            "req_id": "REQ_1",
            "section_number": "2.1",
            "title": "ETWS Behavior",
            "text": "The device shall support ETWS as defined in the spec.",
            "tables": [],
            "images": [],
        },
    ]
    # Empty definitions_map → no expansion
    tree = _tree(reqs, {})
    chunks = builder.build_chunks([tree])
    chunk_tokens = set(tokenize(chunks[0].text))

    # Acronym query still matches (the acronym is in the title/body
    # regardless of the map)
    assert "etws" in chunk_tokens
    # Expansion-only query does NOT match — no expansion was injected
    expansion_tokens = set(tokenize("Earthquake and Tsunami Warning System"))
    assert not (expansion_tokens & chunk_tokens), (
        "Expansion-only query unexpectedly matched a chunk built with "
        "an empty definitions map — expansion must be the only source "
        "of those tokens"
    )
