"""Tests for cross-cell fusion (multi-mno-sira D-DRAFT-10).

I/O (BM25 search, LLM rerank) is injected, so the orchestration is tested
with synthetic data — no bm25x, no live LLM.
"""

from __future__ import annotations

from sandbox.sira_query.fusion import Candidate, fuse, rank_candidates


def _retriever(table):
    """Build a retrieve_fn from {cell: [(doc_id, bm25), ...]}."""
    def _fn(cell, k):
        return table.get(cell, [])[:k]
    return _fn


def _reranker(score_map):
    """Build a rerank_fn from {comp_id: score}. Unlisted comp_ids are
    left unscored (simulating a failed/absent rerank result)."""
    def _fn(query, candidates):
        return {c.comp_id: score_map[c.comp_id]
                for c in candidates if c.comp_id in score_map}
    return _fn


VZW_F = ("VZW", "Feb2026")
VZW_O = ("VZW", "Oct2025")
TMO_J = ("TMO", "Jan2026")


# ── Candidate identity / provenance ──────────────────────────────

def test_candidate_comp_id_is_mno_release_docid():
    c = Candidate(VZW_F, "req:1", 1.0)
    assert c.comp_id == ("VZW", "Feb2026", "req:1")
    assert c.mno == "VZW" and c.release == "Feb2026"


# ── single cell (N=1) — fusion is the general path ───────────────

def test_single_cell_reranked_order():
    table = {VZW_F: [("req:a", 5.0), ("req:b", 4.0), ("req:c", 3.0)]}
    scores = {("VZW", "Feb2026", "req:a"): 10,
              ("VZW", "Feb2026", "req:b"): 90,   # reranker promotes b
              ("VZW", "Feb2026", "req:c"): 50}
    out = fuse("q", {VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=3)
    assert [c.doc_id for c in out] == ["req:b", "req:c", "req:a"]


# ── cross-MNO — balanced retrieval + rerank-score fusion ─────────

def test_cross_mno_rerank_score_sorts_globally():
    table = {
        VZW_F: [("req:v1", 9.0), ("req:v2", 1.0)],   # VZW bm25 high
        TMO_J: [("req:t1", 2.0), ("req:t2", 1.5)],   # TMO bm25 low
    }
    # reranker says TMO's t1 is most relevant despite low BM25 → fusion
    # must rank by rerank score, not BM25 (scales not comparable).
    scores = {
        ("VZW", "Feb2026", "req:v1"): 40,
        ("VZW", "Feb2026", "req:v2"): 20,
        ("TMO", "Jan2026", "req:t1"): 95,
        ("TMO", "Jan2026", "req:t2"): 30,
    }
    out = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=4)
    assert [c.doc_id for c in out] == ["req:t1", "req:v1", "req:t2", "req:v2"]
    # provenance preserved
    assert out[0].cell == TMO_J and out[1].cell == VZW_F


def test_balanced_per_cell_retrieval():
    # per_cell_top_n caps EACH cell, not the pool — both cells contribute.
    table = {
        VZW_F: [(f"v{i}", 10.0 - i) for i in range(10)],
        TMO_J: [(f"t{i}", 10.0 - i) for i in range(10)],
    }
    scores = {c.comp_id: 1 for c in
              [Candidate(VZW_F, f"v{i}", 0) for i in range(2)]
              + [Candidate(TMO_J, f"t{i}", 0) for i in range(2)]}
    out = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=2, top_k=10)
    # 2 per cell = 4 in pool (not 2 total)
    assert len(out) == 4
    assert {c.mno for c in out} == {"VZW", "TMO"}


# ── release-diff — no cross-cell dedup ───────────────────────────

def test_same_docid_in_two_cells_both_survive():
    # The SAME req_id in Oct and Feb (spec evolving) — release-diff needs
    # BOTH. Composite identity keeps them distinct; no doc_id dedup.
    table = {
        VZW_O: [("req:FOO:5.1", 4.0)],
        VZW_F: [("req:FOO:5.1", 4.0)],
    }
    scores = {("VZW", "Oct2025", "req:FOO:5.1"): 70,
              ("VZW", "Feb2026", "req:FOO:5.1"): 80}
    out = fuse("q", {VZW_O, VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=10)
    assert len(out) == 2                          # NOT deduped to 1
    assert {c.release for c in out} == {"Oct2025", "Feb2026"}
    assert out[0].release == "Feb2026"            # higher rerank first


# ── unscored candidates sort last but are not dropped ────────────

def test_missing_rerank_score_sorts_last_not_dropped():
    table = {VZW_F: [("req:a", 5.0), ("req:b", 4.0), ("req:c", 3.0)]}
    # reranker only scores a and c; b's batch "failed" (absent)
    scores = {("VZW", "Feb2026", "req:a"): 30,
              ("VZW", "Feb2026", "req:c"): 60}
    out = fuse("q", {VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=10)
    assert [c.doc_id for c in out] == ["req:c", "req:a", "req:b"]  # b last
    assert len(out) == 3                          # b retained, not dropped
    assert out[-1].rerank_score is None


# ── no-rerank path — round-robin balance ─────────────────────────

def test_no_rerank_round_robin_interleaves_cells():
    table = {
        VZW_F: [("v0", 9.0), ("v1", 8.0)],
        TMO_J: [("t0", 1.0), ("t1", 0.5)],
    }
    # cells sorted: TMO_J < VZW_F (tuple order) → TMO first in round-robin
    out = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=None, per_cell_top_n=10, top_k=4)
    # round-robin: (TMO rank0, VZW rank0, TMO rank1, VZW rank1)
    assert [c.doc_id for c in out] == ["t0", "v0", "t1", "v1"]


def test_no_rerank_single_cell_is_bm25_order():
    table = {VZW_F: [("a", 9.0), ("b", 8.0), ("c", 7.0)]}
    out = fuse("q", {VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=None, per_cell_top_n=10, top_k=2)
    assert [c.doc_id for c in out] == ["a", "b"]


# ── balanced fusion — round-robin under real (non-degenerate) scores ─────

def test_balanced_fusion_keeps_both_cells_under_score_skew():
    # The real-world skew: VZW's chunks all out-score TMO's (sharper chunking),
    # so the default global sort + top_k cut returns mostly VZW. balanced=True
    # must return a cell-balanced top_k instead.
    table = {
        VZW_F: [(f"v{i}", 10.0 - i) for i in range(10)],
        TMO_J: [(f"t{i}", 10.0 - i) for i in range(10)],
    }
    scores = {}
    scores.update({("VZW", "Feb2026", f"v{i}"): 90 - i for i in range(10)})  # 90..81
    scores.update({("TMO", "Jan2026", f"t{i}"): 40 - i for i in range(10)})  # 40..31

    # default: pure global sort → all 6 are VZW (its scores dominate)
    default = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
                   rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=6)
    assert {c.mno for c in default} == {"VZW"}                 # the bug

    # balanced: round-robin within-cell-sorted → 3 VZW / 3 TMO, interleaved
    bal = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=6, balanced=True)
    assert {c.mno for c in bal} == {"VZW", "TMO"}
    assert [c.mno for c in bal] == ["TMO", "VZW", "TMO", "VZW", "TMO", "VZW"]  # TMO_J<VZW_F
    # within each cell still rerank-ordered (best first)
    assert [c.doc_id for c in bal if c.mno == "VZW"] == ["v0", "v1", "v2"]


def test_balanced_single_cell_is_plain_rerank_order():
    # balanced is a no-op for one cell (no cross-cell starvation possible).
    table = {VZW_F: [("a", 5.0), ("b", 4.0), ("c", 3.0)]}
    scores = {("VZW", "Feb2026", "a"): 10, ("VZW", "Feb2026", "b"): 90,
              ("VZW", "Feb2026", "c"): 50}
    out = fuse("q", {VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=10, top_k=3, balanced=True)
    assert [c.doc_id for c in out] == ["b", "c", "a"]          # pure rerank order


# ── degenerate rerank — round-robin instead of cell collapse ─────

def test_all_equal_scores_fall_back_to_round_robin():
    # Reranker returned (or failed to → all-zero) the SAME score for every
    # candidate. A stable score-sort would keep pool order (cell-sorted),
    # so cell-0 takes every top_k slot and the other MNO vanishes. The
    # degenerate-score guard must round-robin instead, keeping both cells.
    table = {
        VZW_F: [("v0", 9.0), ("v1", 8.0)],
        TMO_J: [("t0", 1.0), ("t1", 0.5)],
    }
    zero = {c.comp_id: 0.0 for c in
            [Candidate(VZW_F, "v0", 0), Candidate(VZW_F, "v1", 0),
             Candidate(TMO_J, "t0", 0), Candidate(TMO_J, "t1", 0)]}
    # full pool: round-robin interleave (TMO_J < VZW_F → TMO first)
    out = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(zero), per_cell_top_n=10, top_k=4)
    assert [c.doc_id for c in out] == ["t0", "v0", "t1", "v1"]
    # the sharp case — top_k smaller than the pool must STILL show both MNOs
    # (a pure score-sort would return ["t0","t1"], TMO-only)
    out2 = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
                rerank_fn=_reranker(zero), per_cell_top_n=10, top_k=2)
    assert {c.mno for c in out2} == {"VZW", "TMO"}


# ── top_k cap + empties ──────────────────────────────────────────

def test_top_k_caps_results():
    table = {VZW_F: [(f"r{i}", 10.0 - i) for i in range(20)]}
    scores = {("VZW", "Feb2026", f"r{i}"): 20 - i for i in range(20)}
    out = fuse("q", {VZW_F}, retrieve_fn=_retriever(table),
               rerank_fn=_reranker(scores), per_cell_top_n=20, top_k=5)
    assert len(out) == 5


def test_empty_target_cells():
    out = fuse("q", set(), retrieve_fn=_retriever({}),
               rerank_fn=None, per_cell_top_n=10, top_k=10)
    assert out == []


def test_cell_with_no_hits():
    table = {VZW_F: [], TMO_J: [("t0", 1.0)]}
    out = fuse("q", {VZW_F, TMO_J}, retrieve_fn=_retriever(table),
               rerank_fn=None, per_cell_top_n=10, top_k=10)
    assert [c.doc_id for c in out] == ["t0"]


def test_balanced_cut_is_per_cell_across_three_cells():
    """3-MNO readiness (NORA_SIRA_SCALE_TOPK_BY_CELLS). The service scales the cut to
    top_k * n_cells in balanced multi-cell mode; with that cut, balanced fusion
    keeps the full per-cell budget for EVERY cell instead of starving a 3rd MNO
    to top_k/N. Here: base top_k=40, 3 cells -> cut 120 -> 40 per cell."""
    from collections import Counter
    ATT_J = ("ATT", "Jan2026")
    per_cell = [
        [Candidate(VZW_F, f"a{i}", 1.0) for i in range(40)],
        [Candidate(TMO_J, f"b{i}", 1.0) for i in range(40)],
        [Candidate(ATT_J, f"c{i}", 1.0) for i in range(40)],
    ]
    out = rank_candidates(per_cell, scores=None, top_k=40 * 3, balanced=True)
    per = Counter(c.cell[0] for c in out)
    assert per["VZW"] == per["TMO"] == per["ATT"] == 40   # not 120/3 = 40 each? -> all kept
    assert len(out) == 120


def test_unscaled_cut_would_starve_third_cell():
    """Counterfactual the scaling fixes: the OLD global cut (top_k not scaled)
    splits ~evenly, so each of 3 cells gets only top_k/3 — a borderline chunk
    ranked deeper than that in its cell is dropped."""
    from collections import Counter
    ATT_J = ("ATT", "Jan2026")
    per_cell = [
        [Candidate(VZW_F, f"a{i}", 1.0) for i in range(40)],
        [Candidate(TMO_J, f"b{i}", 1.0) for i in range(40)],
        [Candidate(ATT_J, f"c{i}", 1.0) for i in range(40)],
    ]
    out = rank_candidates(per_cell, scores=None, top_k=40, balanced=True)  # NOT scaled
    per = Counter(c.cell[0] for c in out)
    assert max(per.values()) - min(per.values()) <= 1   # ~13-14 each
    assert all(v < 40 for v in per.values())             # every cell starved vs its 40
