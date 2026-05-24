"""Tests for `core/src/query/reranker.py` — cross-encoder reranker
behind the Reranker Protocol.

Pins:
  - `MockReranker.rerank` is a passthrough (preserves input order
    and length)
  - `CrossEncoderReranker` falls back to passthrough on
    construction failure (model not cached / offline / sentence-
    transformers unavailable) — never raises
  - When the cross-encoder is available and given a stub `predict`
    that returns canned scores, chunks are sorted descending by
    score (stable on ties)
  - Empty / single-chunk inputs are handled trivially
  - Long chunk text is truncated to `max_chunk_chars` before
    scoring (keeps cross-encoder under its token window)
"""

from __future__ import annotations

import pytest

from core.src.query.reranker import (
    CrossEncoderReranker,
    MockReranker,
    OllamaReranker,
    Reranker,
)
from core.src.query.schema import RetrievedChunk


def _chunk(chunk_id: str, text: str = "body", score: float = 0.5) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=text,
        metadata={"req_id": chunk_id.replace("req:", "")},
        similarity_score=score,
        graph_node_id=chunk_id,
    )


# ---------------------------------------------------------------------------
# MockReranker
# ---------------------------------------------------------------------------


def test_mock_returns_input_order_and_length():
    """The default reranker slot — preserves retrieval order so a
    pipeline that "doesn't use" reranking still has a Reranker
    object plugged in."""
    chunks = [_chunk(f"req:{i}") for i in range(5)]
    out = MockReranker().rerank("any query", chunks)
    assert [c.chunk_id for c in out] == [c.chunk_id for c in chunks]
    assert len(out) == len(chunks)


def test_mock_returns_new_list_not_mutating_input():
    """The reranker contract: callers expect a NEW list. Mock
    matches that — defensive against future code that might mutate
    the returned list expecting it's safe."""
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = MockReranker().rerank("q", chunks)
    out.pop()
    assert len(chunks) == 2  # input untouched


def test_mock_empty_input_returns_empty():
    assert MockReranker().rerank("q", []) == []


def test_mock_satisfies_reranker_protocol():
    """`Reranker` is a runtime-checkable Protocol; both the Mock
    and any future class must satisfy structural typing."""
    assert isinstance(MockReranker(), Reranker)


# ---------------------------------------------------------------------------
# CrossEncoderReranker — graceful degradation + sort behavior
# ---------------------------------------------------------------------------


def test_cross_encoder_unavailable_degrades_to_passthrough():
    """When sentence-transformers can't load the requested model
    (typical in CI / first-time runs / offline boxes), the reranker
    constructs without raising and reports `available=False`.
    `rerank` then returns input order unchanged."""
    # Use a deliberately bogus model id that's guaranteed not in cache.
    r = CrossEncoderReranker(model_name="this/does-not-exist-xyz-12345")
    assert r.available is False

    chunks = [_chunk(f"req:{i}") for i in range(3)]
    out = r.rerank("anything", chunks)
    # Input order preserved
    assert [c.chunk_id for c in out] == [c.chunk_id for c in chunks]


def test_cross_encoder_with_stub_model_sorts_descending_by_score():
    """When the model IS available, chunks should sort by descending
    relevance score. Inject a stub `_model.predict` to test the sort
    logic without needing a real cross-encoder load."""
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    r._model_name = "stub"
    r._device = "cpu"
    r._batch_size = 32
    r._max_chunk_chars = 4000
    r._available = True

    class _StubModel:
        def predict(self, pairs, batch_size, show_progress_bar):
            # Score is the index in chunk_id (req:0 → 0.0, req:1 → 1.0, etc.)
            # so chunks with higher index rank higher
            return [float(c.split(":")[1]) for _, c in pairs]

    r._model = _StubModel()

    chunks = [_chunk(f"req:{i}", text=f"req:{i}") for i in range(5)]
    out = r.rerank("any query", chunks)
    # Should be reversed: req:4, req:3, req:2, req:1, req:0
    assert [c.chunk_id for c in out] == ["req:4", "req:3", "req:2", "req:1", "req:0"]


def test_cross_encoder_predict_failure_falls_back_to_input_order():
    """If the model raises during `predict` (mid-call timeout, OOM,
    etc.), the reranker logs and returns the chunks in input order
    rather than crashing the retrieval path."""
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    r._model_name = "stub"
    r._device = "cpu"
    r._batch_size = 32
    r._max_chunk_chars = 4000
    r._available = True

    class _FailingModel:
        def predict(self, pairs, batch_size, show_progress_bar):
            raise RuntimeError("simulated cross-encoder failure")

    r._model = _FailingModel()

    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b", "req:c"]


def test_cross_encoder_single_chunk_no_scoring():
    """Single-chunk input is a degenerate case — no reordering
    possible. Avoid the model call entirely."""
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    r._available = True
    r._model_name = "stub"
    r._device = "cpu"
    r._batch_size = 32
    r._max_chunk_chars = 4000

    class _NeverCalledModel:
        def predict(self, *args, **kwargs):
            raise AssertionError("predict should not be called")
    r._model = _NeverCalledModel()

    out = r.rerank("q", [_chunk("req:only")])
    assert [c.chunk_id for c in out] == ["req:only"]


def test_cross_encoder_truncates_long_chunks_before_scoring():
    """Cross-encoders have token windows; long chunks must be
    truncated to `max_chunk_chars` before scoring. Capture what the
    stub receives to verify."""
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    r._available = True
    r._model_name = "stub"
    r._device = "cpu"
    r._batch_size = 32
    r._max_chunk_chars = 100

    captured = {}

    class _CaptureModel:
        def predict(self, pairs, batch_size, show_progress_bar):
            captured["pairs"] = list(pairs)
            return [0.5] * len(pairs)
    r._model = _CaptureModel()

    long_text = "x" * 1000
    short_text = "y" * 50
    out = r.rerank(
        "q",
        [_chunk("req:long", text=long_text), _chunk("req:short", text=short_text)],
    )

    # Both pairs were submitted; long was truncated, short untouched
    submitted_texts = [text for _, text in captured["pairs"]]
    assert len(submitted_texts[0]) == 100  # truncated
    assert len(submitted_texts[1]) == 50   # untouched
    # Output order preserved (equal scores → stable sort)
    assert {c.chunk_id for c in out} == {"req:long", "req:short"}


def test_cross_encoder_stable_on_ties():
    """When two chunks tie on cross-encoder score, their input
    order must be preserved — a stable sort. Important because the
    input order is the RRF-fused order, which has its own meaning."""
    r = CrossEncoderReranker.__new__(CrossEncoderReranker)
    r._available = True
    r._model_name = "stub"
    r._device = "cpu"
    r._batch_size = 32
    r._max_chunk_chars = 4000

    class _UniformScoreModel:
        def predict(self, pairs, batch_size, show_progress_bar):
            # All chunks score equally → sort must preserve order
            return [0.5] * len(pairs)
    r._model = _UniformScoreModel()

    chunks = [_chunk(f"req:{c}") for c in "abcde"]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == [c.chunk_id for c in chunks]


# ── OllamaReranker ──────────────────────────────────────────────────


def _make_ollama_reranker_score_mode(
    score_map: dict[str, float] | None = None,
    endpoint: str = "/api/embed",
):
    """Build an OllamaReranker in true-reranker (score) mode, bypassing
    network probes. `_embed_raw` is stubbed to return a single-element
    vector — the score. Defaults to scoring by passage-text length."""
    r = OllamaReranker.__new__(OllamaReranker)
    r._model_name = "bbjson/bge-reranker-base:latest"
    r._base_url = "http://127.0.0.1:11434"
    r._timeout = 60
    r._max_chunk_chars = 4000
    r._opener = None
    r._endpoint = endpoint
    r._mode = "rerank_score"
    r._available = True

    def _stub_embed(_endpoint, text):
        # In score mode, text = "query<sep>passage". Score = explicit
        # map (passage as key) or fallback to passage length.
        if score_map is not None:
            for passage, score in score_map.items():
                if text.endswith(passage):
                    return [score]
        # Default — extract passage from concatenated input
        if r._PAIR_SEP in text:
            _, passage = text.split(r._PAIR_SEP, 1)
            return [float(len(passage))]
        return [float(len(text))]

    r._embed_raw = _stub_embed
    return r


def _make_ollama_reranker_similarity_mode(
    query_vec: list[float] | None = None,
    passage_vecs: dict[str, list[float]] | None = None,
):
    """Build an OllamaReranker in embedding-similarity mode. Stubs
    `_embed_raw` to return query_vec for queries and lookup-by-text
    vectors for passages."""
    r = OllamaReranker.__new__(OllamaReranker)
    r._model_name = "bbjson/bge-reranker-base:latest"
    r._base_url = "http://127.0.0.1:11434"
    r._timeout = 60
    r._max_chunk_chars = 4000
    r._opener = None
    r._endpoint = "/api/embed"
    r._mode = "embedding_similarity"
    r._available = True

    qv = query_vec or [1.0, 0.0]
    pvs = passage_vecs or {}

    def _stub_embed(_endpoint, text):
        if text == "query":
            return qv
        if text in pvs:
            return pvs[text]
        return [0.0, 0.0]

    r._embed_raw = _stub_embed
    return r


def test_ollama_reranker_unavailable_when_ollama_unreachable():
    """Construction against a bogus URL → marks unavailable, doesn't raise."""
    r = OllamaReranker(
        model_name="any:latest",
        base_url="http://127.0.0.1:1",  # nothing listens here
        timeout=1,
    )
    assert r.available is False
    chunks = [_chunk(f"req:{i}") for i in range(3)]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == [c.chunk_id for c in chunks]


def test_ollama_reranker_score_mode_sorts_descending():
    r = _make_ollama_reranker_score_mode()
    chunks = [
        _chunk("req:short", text="A"),       # score 1
        _chunk("req:medium", text="AAAAA"),  # score 5
        _chunk("req:long", text="A" * 20),   # score 20
    ]
    out = r.rerank("anything", chunks)
    assert [c.chunk_id for c in out] == ["req:long", "req:medium", "req:short"]


def test_ollama_reranker_score_mode_explicit_map():
    score_map = {"doc-a": 0.1, "doc-b": 0.9, "doc-c": 0.5}
    r = _make_ollama_reranker_score_mode(score_map=score_map)
    chunks = [
        _chunk("req:a", text="doc-a"),
        _chunk("req:b", text="doc-b"),
        _chunk("req:c", text="doc-c"),
    ]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:b", "req:c", "req:a"]


def test_ollama_reranker_similarity_mode_sorts_by_cosine():
    """Embedding-similarity mode: cosine of query vector with each
    passage vector decides ordering."""
    # Query is [1, 0]. Three passages with different angles:
    # - "doc-aligned" is [1, 0]   → cosine = 1.0
    # - "doc-half"    is [1, 1]   → cosine ≈ 0.707
    # - "doc-orth"    is [0, 1]   → cosine = 0.0
    r = _make_ollama_reranker_similarity_mode(
        query_vec=[1.0, 0.0],
        passage_vecs={
            "doc-aligned": [1.0, 0.0],
            "doc-half": [1.0, 1.0],
            "doc-orth": [0.0, 1.0],
        },
    )
    chunks = [
        _chunk("req:orth", text="doc-orth"),
        _chunk("req:aligned", text="doc-aligned"),
        _chunk("req:half", text="doc-half"),
    ]
    out = r.rerank("query", chunks)
    assert [c.chunk_id for c in out] == ["req:aligned", "req:half", "req:orth"]


def test_ollama_reranker_passthrough_when_unavailable():
    r = _make_ollama_reranker_score_mode()
    r._available = False  # simulate failed probe
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:1", "req:2"]


def test_ollama_reranker_passthrough_on_single_chunk():
    r = _make_ollama_reranker_score_mode()
    out = r.rerank("q", [_chunk("req:only")])
    assert [c.chunk_id for c in out] == ["req:only"]


def test_ollama_reranker_passthrough_on_empty_chunks():
    r = _make_ollama_reranker_score_mode()
    assert r.rerank("q", []) == []


def test_ollama_reranker_score_failure_falls_back_to_input_order():
    """If a scoring call throws mid-pair, the whole rerank falls back
    to passthrough — graceful degradation."""
    r = _make_ollama_reranker_score_mode()

    def _flaky_embed(endpoint, text):
        if text.endswith("doc-b"):
            raise RuntimeError("simulated wire failure")
        return [1.0]

    r._embed_raw = _flaky_embed
    chunks = [
        _chunk("req:a", text="doc-a"),
        _chunk("req:b", text="doc-b"),
        _chunk("req:c", text="doc-c"),
    ]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b", "req:c"]


def test_ollama_reranker_score_mode_wrong_shape_falls_back():
    """If the model unexpectedly returns multi-dim vectors mid-rerank
    (shape changed since probe), preserve input order."""
    r = _make_ollama_reranker_score_mode()

    def _wrong_shape(endpoint, text):
        return [0.1, 0.2, 0.3]  # 3-dim, not 1-dim

    r._embed_raw = _wrong_shape
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_ollama_reranker_similarity_mode_handles_zero_norm():
    """When the query vector has zero norm (e.g. all zeros), cosine
    is defined as 0.0 — ordering preserved (stable sort)."""
    r = _make_ollama_reranker_similarity_mode(
        query_vec=[0.0, 0.0],
        passage_vecs={
            "doc-a": [1.0, 0.0],
            "doc-b": [0.0, 1.0],
        },
    )
    chunks = [_chunk("req:a", text="doc-a"), _chunk("req:b", text="doc-b")]
    out = r.rerank("query", chunks)
    # All scores = 0.0, stable sort preserves input order
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_ollama_reranker_satisfies_reranker_protocol():
    """Duck-typing check: OllamaReranker is interchangeable with the
    other Reranker implementations."""
    r = _make_ollama_reranker_score_mode()
    assert isinstance(r, Reranker)


def test_ollama_reranker_cosine_helper():
    """Cosine helper returns 0.0 for degenerate inputs."""
    cos = OllamaReranker._cosine
    assert cos([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cos([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert abs(cos([1.0, 1.0], [1.0, 0.0]) - 0.7071) < 0.001
    # Degenerate
    assert cos([], [1.0]) == 0.0
    assert cos([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cos([1.0, 0.0], [1.0]) == 0.0  # mismatched dims
