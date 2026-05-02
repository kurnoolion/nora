"""Unit tests for BM25 sparse retrieval (`bm25_index.py`).

Covers:
  - telecom-aware tokenizer preserves req-ids and spec numbers
  - BM25Index search ranks specific-term matches above generic ones
  - filter_ids gate honored
  - rrf_fuse math + ordering
  - empty-input edge cases
  - from_store fallback when get_all is unavailable / empty
"""

from __future__ import annotations

import pytest

from core.src.query.bm25_index import BM25Index, rrf_fuse, tokenize
from core.src.vectorstore.store_base import QueryResult


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def test_tokenize_preserves_req_id_as_single_token():
    """Req-ids like VZ_REQ_LTEAT_45 must NOT be split on underscores —
    BM25 needs them as one token to score exact-id queries highly."""
    toks = tokenize("VZ_REQ_LTEAT_45 references TS 24.301 Rel-9")
    assert "vz_req_lteat_45" in toks
    assert "24.301" in toks
    assert "rel-9" in toks
    # The TS abbreviation survives
    assert "ts" in toks


def test_tokenize_drops_punctuation_and_short_tokens():
    """Sentence-final punctuation strips; 1-char tokens are dropped."""
    toks = tokenize("What is requirement VZ_REQ_LTEDATARETRY_7754?")
    assert "vz_req_ltedataretry_7754" in toks
    # `?` strips; "is" is len-2, kept
    assert "is" in toks
    # Empty/None input
    assert tokenize("") == []


def test_tokenize_preserves_specific_terms():
    """T3402, cause code 22, spec versions — all the high-IDF tokens."""
    toks = tokenize("T3402 timer; cause code 22; 3GPP TS 24.301 Section 5.5.1.2")
    assert "t3402" in toks
    assert "22" in toks  # numeric codes preserved
    assert "3gpp" in toks
    assert "24.301" in toks
    assert "5.5.1.2" in toks  # dotted section number stays whole


# ---------------------------------------------------------------------------
# BM25Index.search
# ---------------------------------------------------------------------------


def _index(*chunks: tuple[str, str]) -> BM25Index:
    """Build an index from (id, text) tuples; metadata is empty dict."""
    ids = [c[0] for c in chunks]
    texts = [c[1] for c in chunks]
    metas = [{} for _ in chunks]
    return BM25Index(ids, texts, metas)


def test_search_ranks_exact_specific_term_above_generic():
    """A query for a rare exact term should surface the chunk that
    contains it ahead of chunks that don't. Uses a realistic-sized
    corpus so IDF reflects the term's actual rarity (in tiny corpora
    BM25's IDF can flip when a "rare" term appears in most docs)."""
    chunks: list[tuple[str, str]] = [
        ("target", "T3402 is the EMM attempt-counter-restart timer per TS 24.301."),
    ]
    # Realistic 30-doc corpus where T3402 only appears in `target`.
    for i in range(30):
        chunks.append((
            f"filler_{i}",
            f"Discussion of LTE topic {i}: "
            f"covers PDN connectivity, attach handling, retry behavior, "
            f"and NAS messaging.",
        ))
    idx = _index(*chunks)
    results = idx.search("T3402 timer behavior", top_k=5)
    ranked_ids = [r[0] for r in results]
    assert ranked_ids[0] == "target", (
        f"target chunk (sole T3402 holder) should rank first; got {ranked_ids}"
    )


def test_search_filter_ids_gates_results():
    """When `filter_ids` is supplied, no chunk outside the set is
    returned — even if it would rank highly otherwise."""
    idx = _index(
        ("c1", "T3402 timer behavior"),
        ("c2", "T3402 in detail"),
        ("c3", "Unrelated LTE topic"),
    )
    # Without filter, c1/c2 win
    unfiltered = [cid for cid, _ in idx.search("T3402", top_k=5)]
    assert "c1" in unfiltered and "c2" in unfiltered
    # With filter restricted to {c3}, only c3 returns (score may be 0)
    filtered = idx.search("T3402", top_k=5, filter_ids={"c3"})
    assert all(cid == "c3" for cid, _ in filtered)


def test_search_empty_query_returns_empty():
    idx = _index(("c1", "anything"))
    assert idx.search("", top_k=5) == []
    # Query that tokenizes to nothing (single chars, just punctuation)
    assert idx.search("? !", top_k=5) == []


def test_index_construction_rejects_empty_or_mismatched():
    with pytest.raises(ValueError):
        BM25Index([], [], [])
    with pytest.raises(ValueError):
        BM25Index(["a"], ["text1", "text2"], [{}])


def test_chunk_text_and_metadata_lookup():
    """Used by hybrid retrieval to materialize BM25-only ids without
    a second store round-trip."""
    idx = BM25Index(
        chunk_ids=["c1", "c2"],
        chunk_texts=["first", "second"],
        chunk_metadatas=[{"plan_id": "P1"}, {"plan_id": "P2"}],
    )
    assert idx.chunk_text("c1") == "first"
    assert idx.chunk_metadata("c2") == {"plan_id": "P2"}
    assert idx.chunk_text("missing") == ""
    assert idx.chunk_metadata("missing") == {}


# ---------------------------------------------------------------------------
# from_store fallback
# ---------------------------------------------------------------------------


class _StoreNoGetAll:
    """Stand-in for an older VectorStoreProvider without get_all()."""
    pass


class _EmptyStore:
    def get_all(self):
        return QueryResult(ids=[], documents=[], metadatas=[], distances=[])


class _PopulatedStore:
    def get_all(self):
        return QueryResult(
            ids=["c1", "c2"],
            documents=["text one", "text two"],
            metadatas=[{}, {}],
            distances=[],
        )


def test_from_store_returns_none_when_store_lacks_get_all():
    """Older protocol implementations without get_all are tolerated:
    BM25 disables, hybrid retrieval falls back to pure dense."""
    assert BM25Index.from_store(_StoreNoGetAll()) is None


def test_from_store_returns_none_for_empty_store():
    """Empty store → no BM25; pure-dense fallback."""
    assert BM25Index.from_store(_EmptyStore()) is None


def test_from_store_builds_index_on_populated_store():
    idx = BM25Index.from_store(_PopulatedStore())
    assert idx is not None
    assert idx.size == 2


# ---------------------------------------------------------------------------
# rrf_fuse
# ---------------------------------------------------------------------------


def test_rrf_fuse_orders_by_combined_score():
    """A chunk that ranks highly in BOTH inputs should beat one that
    ranks highly in ONE — the whole point of RRF."""
    dense = ["a", "b", "c"]   # ranks 1, 2, 3
    bm25  = ["b", "a", "d"]   # ranks 1, 2, 3
    fused = rrf_fuse(dense, bm25)
    fused_ids = [cid for cid, _ in fused]
    # `a` is at rank 1 in dense, rank 2 in bm25 → 1/61 + 1/62
    # `b` is at rank 2 in dense, rank 1 in bm25 → 1/62 + 1/61 — same total
    # `c` only in dense → 1/63
    # `d` only in bm25 → 1/63
    # a and b should tie; c and d should tie below them.
    assert set(fused_ids[:2]) == {"a", "b"}
    assert set(fused_ids[2:]) == {"c", "d"}


def test_rrf_fuse_with_top_k_cap():
    """top_k caps the output but doesn't change ordering."""
    fused = rrf_fuse(["a", "b", "c", "d"], ["a", "b", "c", "d"], top_k=2)
    assert len(fused) == 2
    assert [cid for cid, _ in fused] == ["a", "b"]


def test_rrf_fuse_handles_disjoint_inputs():
    """Two ranked lists with no overlap — fused score is just 1/(60+rank)
    from one side."""
    fused = rrf_fuse(["a", "b"], ["c", "d"])
    fused_ids = [cid for cid, _ in fused]
    # `a` rank 1 in dense → 1/61; `c` rank 1 in bm25 → 1/61. Tie.
    # `b` rank 2 → 1/62. `d` rank 2 → 1/62. Tie.
    assert set(fused_ids[:2]) == {"a", "c"}
    assert set(fused_ids[2:]) == {"b", "d"}


def test_rrf_fuse_with_no_inputs():
    assert rrf_fuse() == []


def test_rrf_fuse_weights_bias_one_retriever():
    """When dense is weighted 1.0 and BM25 is weighted 0.5, a chunk
    that ranks high in dense should beat a chunk that ranks high in
    BM25 only — the production hybrid setting."""
    dense = ["a", "b", "c"]   # a=rank 1, b=rank 2, c=rank 3
    bm25  = ["x", "y", "a"]   # x=rank 1, y=rank 2, a=rank 3
    fused = rrf_fuse(dense, bm25, weights=[1.0, 0.5])
    fused_ids = [cid for cid, _ in fused]
    # `a`: 1.0/(60+1) + 0.5/(60+3) = 0.0164 + 0.0079 = 0.0243
    # `x`: 0.5/(60+1)              = 0.0082
    # With dense double-weighted, `a` clearly beats `x`.
    assert fused_ids[0] == "a"
    # `x` and `b` should be close (b: 1.0/62 = 0.0161; x: 0.0082).
    # `b` wins.
    assert fused_ids[1] == "b"


def test_rrf_fuse_rejects_mismatched_weights_length():
    with pytest.raises(ValueError):
        rrf_fuse(["a"], ["b"], weights=[1.0, 1.0, 1.0])


def test_rrf_fuse_k_constant_dampens_tail():
    """Larger k flattens the ranking curve (rank 1 vs rank 100 are
    closer in score). Default k=60 matches the Cormack 2009 paper."""
    fused_k60 = dict(rrf_fuse(["a", "b"], ["c", "d"], k=60))
    fused_k1 = dict(rrf_fuse(["a", "b"], ["c", "d"], k=1))
    # With k=1, top-rank chunks dominate more strongly
    assert fused_k1["a"] > fused_k60["a"]
    # With k=60, the top vs the second-ranked are closer in score
    ratio_k60 = fused_k60["a"] / fused_k60["b"]
    ratio_k1 = fused_k1["a"] / fused_k1["b"]
    assert ratio_k1 > ratio_k60
