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


def _make_ollama_reranker(score_map: dict[tuple[str, str], float] | None = None,
                          endpoint: str = "/api/embed"):
    """Build an OllamaReranker bypassing __init__ network probes, with
    a stubbed `_score_pair_via` driven by score_map (defaults to scoring
    by chunk text length descending)."""
    r = OllamaReranker.__new__(OllamaReranker)
    r._model_name = "bbjson/bge-reranker-base:latest"
    r._base_url = "http://127.0.0.1:11434"
    r._timeout = 60
    r._max_chunk_chars = 4000
    r._opener = None
    r._endpoint = endpoint
    r._available = True

    def _stub_score(_endpoint, query, passage):
        if score_map is not None:
            key = (query, passage)
            if key in score_map:
                return score_map[key]
        # Default: passage length is the score (longer = more relevant)
        return float(len(passage))

    r._score_pair_via = _stub_score
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


def test_ollama_reranker_sorts_descending_by_score():
    r = _make_ollama_reranker()
    chunks = [
        _chunk("req:short", text="A"),       # score 1
        _chunk("req:medium", text="AAAAA"),  # score 5
        _chunk("req:long", text="A" * 20),   # score 20
    ]
    out = r.rerank("anything", chunks)
    assert [c.chunk_id for c in out] == ["req:long", "req:medium", "req:short"]


def test_ollama_reranker_explicit_score_map():
    score_map = {
        ("q", "doc-a"): 0.1,
        ("q", "doc-b"): 0.9,
        ("q", "doc-c"): 0.5,
    }
    r = _make_ollama_reranker(score_map=score_map)
    chunks = [
        _chunk("req:a", text="doc-a"),
        _chunk("req:b", text="doc-b"),
        _chunk("req:c", text="doc-c"),
    ]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:b", "req:c", "req:a"]


def test_ollama_reranker_passthrough_when_unavailable():
    r = _make_ollama_reranker()
    r._available = False  # simulate failed probe
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:1", "req:2"]


def test_ollama_reranker_passthrough_on_single_chunk():
    r = _make_ollama_reranker()
    out = r.rerank("q", [_chunk("req:only")])
    assert [c.chunk_id for c in out] == ["req:only"]


def test_ollama_reranker_passthrough_on_empty_chunks():
    r = _make_ollama_reranker()
    assert r.rerank("q", []) == []


def test_ollama_reranker_score_failure_falls_back_to_input_order():
    """If a single scoring call throws, the whole rerank falls back to
    passthrough — graceful degradation matching CrossEncoderReranker."""
    r = _make_ollama_reranker()

    def _flaky_score(endpoint, query, passage):
        if passage == "doc-b":
            raise RuntimeError("simulated wire failure")
        return 1.0

    r._score_pair_via = _flaky_score
    chunks = [
        _chunk("req:a", text="doc-a"),
        _chunk("req:b", text="doc-b"),
        _chunk("req:c", text="doc-c"),
    ]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b", "req:c"]


def test_ollama_reranker_none_score_falls_back_to_input_order():
    """If a scoring call returns None (multi-dim vector response —
    model is acting as embedder, not reranker), preserve input order."""
    r = _make_ollama_reranker()

    def _none_score(endpoint, query, passage):
        return None

    r._score_pair_via = _none_score
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_ollama_reranker_satisfies_reranker_protocol():
    """Duck-typing check: OllamaReranker is interchangeable with the
    other Reranker implementations."""
    r = _make_ollama_reranker()
    assert isinstance(r, Reranker)  # Protocol check via runtime_checkable
