"""Cross-cell fusion for multi-MNO SIRA (multi-mno-sira D-DRAFT-10).

The retrieve-per-cell -> tag -> merge -> rerank loop that turns N per-cell
BM25 result lists into one ranking. Deliberately decoupled from the I/O:
BM25 search and LLM rerank are injected as callables, so the orchestration
(the bug-prone cross-cell identity threading) is unit-testable without
bm25x or a live LLM. `service.py` supplies the real callables.

Single-cell is N=1 — there is no separate cross-cell branch; the three
query shapes (scoped / cross-MNO / release-diff) collapse to this one
loop, differing only in which cells `resolve_cells` returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sandbox.sira_cells import CellKey

# A cell-local BM25 hit: (doc_id, bm25_score). doc_ids are cell-local.
Hit = tuple[str, float]
# Maps a candidate's composite id -> rerank score (absolute, 0-100).
CompId = tuple[str, str, str]  # (mno, release, doc_id)

RetrieveFn = Callable[[CellKey, int], list[Hit]]
RerankFn = Callable[[str, "list[Candidate]"], dict[CompId, float]]


@dataclass
class Candidate:
    """A retrieved chunk tagged with its origin cell (provenance).

    The origin cell IS the provenance (D-DRAFT-4) — attached at
    retrieval, not encoded in the doc_id. The composite `comp_id`
    `(mno, release, doc_id)` is the cross-cell identity: the same
    doc_id in two cells (release-diff) stays distinct.
    """
    cell: CellKey
    doc_id: str
    bm25_score: float
    rerank_score: float | None = None

    @property
    def comp_id(self) -> CompId:
        return (self.cell[0], self.cell[1], self.doc_id)

    @property
    def mno(self) -> str:
        return self.cell[0]

    @property
    def release(self) -> str:
        return self.cell[1]


def _round_robin(per_cell: list[list[Candidate]]) -> list[Candidate]:
    """Interleave per-cell lists position-by-position (cell 0 rank 0,
    cell 1 rank 0, …, cell 0 rank 1, …). Used by the no-rerank path to
    keep cross-cell balance WITHOUT comparing BM25 scores (which aren't
    comparable across cells — different per-corpus IDF scales)."""
    out: list[Candidate] = []
    width = max((len(c) for c in per_cell), default=0)
    for i in range(width):
        for cell_list in per_cell:
            if i < len(cell_list):
                out.append(cell_list[i])
    return out


def fuse(
    query: str,
    target_cells: set[CellKey],
    *,
    retrieve_fn: RetrieveFn,
    rerank_fn: RerankFn | None,
    per_cell_top_n: int,
    top_k: int,
) -> list[Candidate]:
    """Retrieve per cell, merge, rerank the union, return top_k.

    - **Balanced retrieval** (FR-multi-3): each cell contributes up to
      `per_cell_top_n` candidates, so neither MNO is starved by
      vocabulary skew. Cross-cell rerank cost is therefore
      ~ len(target_cells) * per_cell_top_n.
    - **Fusion method** (D-DRAFT-10 call 1): when a reranker is given,
      sort the merged pool by the reranker's absolute 0-100 score
      (comparable across cells), NOT RRF. A candidate the reranker
      didn't score (e.g. a failed rerank batch) keeps `rerank_score
      None` and sorts to the bottom — it is NOT dropped before top_k.
    - **No-rerank path**: round-robin interleave per cell (balanced).
      BM25 scores aren't comparable across cells, so a global BM25 sort
      would be meaningless for >1 cell; round-robin preserves balance.
      For a single cell it degenerates to the cell's own BM25 order.
    - **No cross-cell dedup** (D-DRAFT-10 call 3): the same doc_id in
      two cells (release-diff) yields two Candidates with distinct
      `comp_id`; both can reach top_k.

    `retrieve_fn(cell, k)` returns that cell's top-k `(doc_id, bm25)`
    hits (the caller bakes the shared query-expansion into the closure).
    `rerank_fn(query, candidates)` returns `{comp_id: score}`.
    """
    per_cell: list[list[Candidate]] = []
    for cell in sorted(target_cells):
        hits = retrieve_fn(cell, per_cell_top_n)
        per_cell.append([Candidate(cell, did, score) for did, score in hits])

    pool: list[Candidate] = [c for cell_list in per_cell for c in cell_list]
    if not pool:
        return []

    if rerank_fn is not None:
        scores = rerank_fn(query, pool)
        for c in pool:
            c.rerank_score = scores.get(c.comp_id)
        # scored-above-unscored, then by score desc; stable on ties.
        pool.sort(
            key=lambda c: (c.rerank_score is not None, c.rerank_score or 0.0),
            reverse=True,
        )
        return pool[:top_k]

    # No reranker: balanced round-robin (BM25 scores not cross-comparable).
    return _round_robin(per_cell)[:top_k]
