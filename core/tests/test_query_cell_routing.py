"""Query-side cell routing (D-DRAFT-11 slice 2).

Covers QueryPipeline's per-cell retriever construction + `_select_cells`
routing, plus single-store back-compat (a flat store normalizes to FLAT_CELL).
Full retrieve→merge→rerank is exercised on real corpora; the per-cell
retriever + reranker are unit-tested elsewhere — this pins the routing.
"""

from __future__ import annotations

import networkx as nx

from core.src.query.pipeline import QueryPipeline
from core.src.query.schema import MNOScope, QueryIntent, ScopedQuery
from core.src.vectorstore.cell_loader import FLAT_CELL
from core.src.vectorstore.store_base import QueryResult

VZW = ("VZW-OA", "Feb2026")
MNOB = ("MNO-B", "Mar2025")


class _StubEmbedder:
    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]

    @property
    def dimension(self):
        return 2

    @property
    def model_name(self):
        return "stub"


class _StubStore:
    def __init__(self, items=()):
        self._items = list(items)  # (id, text, meta)

    @property
    def count(self):
        return len(self._items)

    def get_all(self):
        return QueryResult(
            ids=[i[0] for i in self._items],
            documents=[i[1] for i in self._items],
            metadatas=[i[2] for i in self._items],
            distances=[],
        )

    def query(self, query_embedding, n_results=10, where=None):
        items = self._items[:n_results]
        return QueryResult(
            ids=[i[0] for i in items],
            documents=[i[1] for i in items],
            metadatas=[i[2] for i in items],
            distances=[0.1] * len(items),
        )


def _pipeline(cell_stores=None, store=None):
    return QueryPipeline(
        graph=nx.DiGraph(), embedder=_StubEmbedder(),
        store=store, cell_stores=cell_stores, enable_bm25=False,
    )


def _scoped(*pairs):
    return ScopedQuery(
        intent=QueryIntent(raw_query="q"),
        scoped_mnos=[MNOScope(m, r) for m, r in pairs],
    )


class TestCellRouting:
    def test_builds_one_retriever_per_cell(self):
        p = _pipeline({VZW: _StubStore(), MNOB: _StubStore()})
        assert set(p._retrievers) == {VZW, MNOB}
        assert len(p._stores) == 2

    def test_select_cells_matches_scope(self):
        p = _pipeline({VZW: _StubStore(), MNOB: _StubStore()})
        assert set(p._select_cells(_scoped(VZW, MNOB))) == {VZW, MNOB}
        assert p._select_cells(_scoped(VZW)) == [VZW]

    def test_select_cells_falls_back_to_all_when_no_match(self):
        p = _pipeline({VZW: _StubStore(), MNOB: _StubStore()})
        assert set(p._select_cells(_scoped(("UNKNOWN", "Feb2026")))) == {VZW, MNOB}

    def test_single_store_backcompat_is_flat_cell(self):
        p = _pipeline(cell_stores=None, store=_StubStore())
        assert set(p._retrievers) == {FLAT_CELL}
        # any scope falls back to the single flat cell
        assert p._select_cells(_scoped(VZW)) == [FLAT_CELL]
