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


# ─────────────────────────────────────────────────────────────────────
# OpenAIRerankChat — per-pair chat-completions scoring
# ─────────────────────────────────────────────────────────────────────


def _stub_urlopen(monkeypatch, target_module, responses):
    """Patch urllib.request.urlopen inside the given module's namespace
    so each call returns the next response in `responses` (an iterable
    of (status, body_str) or Exception).

    Returns the list of captured request payloads (one dict per call)
    so tests can assert on what was sent.
    """
    import io
    import json
    import urllib.request
    sent = []
    it = iter(responses)

    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._body = body
        def read(self):
            return self._body.encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        try:
            r = next(it)
        except StopIteration:
            r = (200, '{"choices":[{"message":{"content":"0"}}]}')
        if isinstance(r, Exception):
            raise r
        status, body = r
        return _FakeResp(status, body)

    # The reranker classes do `import urllib.request` inside their
    # methods, so patching the global `urllib.request.urlopen` is what
    # actually intercepts the call. `target_module` is kept in the
    # signature for symmetry with future tests that need to patch
    # something module-scoped, but isn't used here.
    del target_module
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return sent


def _ok(score: int) -> tuple[int, str]:
    """A 200 response carrying a chat-completion that says the integer."""
    import json
    return (200, json.dumps({
        "choices": [{"message": {"content": str(score)}}],
    }))


def test_openai_rerank_chat_unavailable_on_empty_base_url():
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(model_name="m", base_url="")
    assert r.available is False
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:1", "req:2"]


def test_openai_rerank_chat_sorts_descending_by_score(monkeypatch):
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _ok(20),  # for req:a
        _ok(80),  # for req:b
        _ok(50),  # for req:c
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:b", "req:c", "req:a"]


def test_openai_rerank_chat_parses_score_from_surrounding_text(monkeypatch):
    """Models often emit 'Score: 42' or 'The score is 42.' — the parser
    must extract the first integer."""
    from core.src.query.reranker import OpenAIRerankChat
    import json
    r = OpenAIRerankChat(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, json.dumps({"choices": [{"message": {"content": "Score: 95"}}]})),
        (200, json.dumps({"choices": [{"message": {"content": "I'd say about 30 out of 100."}}]})),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_openai_rerank_chat_clamps_out_of_range_scores(monkeypatch):
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(model_name="m", base_url="http://h")
    # Model returns 150 (clamped to 100) and -20 (clamped to 0).
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _ok(150),
        _ok(-20),
    ])
    chunks = [_chunk("req:high"), _chunk("req:low")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:high", "req:low"]


def test_openai_rerank_chat_per_call_failure_scores_zero(monkeypatch):
    """A single failed call must not sink the whole rerank — that chunk
    just sinks to the bottom with score 0."""
    from core.src.query.reranker import OpenAIRerankChat
    import urllib.error
    r = OpenAIRerankChat(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _ok(40),                                       # req:a
        urllib.error.URLError("connection refused"),   # req:b → score 0
        _ok(70),                                       # req:c
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:c", "req:a", "req:b"]


def test_openai_rerank_chat_sends_bearer_token_when_set(monkeypatch):
    from core.src.query.reranker import OpenAIRerankChat
    import urllib.request
    captured: dict = {}

    class _FakeResp:
        status = 200
        def read(self): return b'{"choices":[{"message":{"content":"50"}}]}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _grab(req, timeout=None):
        captured.update(req.headers)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _grab)

    r = OpenAIRerankChat(model_name="m", base_url="http://h", api_key="sk-xyz")
    r.rerank("q", [_chunk("req:a")])
    # urllib lowercases the keys — check via case-insensitive lookup
    auth = next((v for k, v in captured.items() if k.lower() == "authorization"), None)
    assert auth == "Bearer sk-xyz"


def test_openai_rerank_chat_satisfies_reranker_protocol():
    from core.src.query.reranker import OpenAIRerankChat, Reranker
    assert isinstance(
        OpenAIRerankChat(model_name="m", base_url="http://h"), Reranker,
    )


# ─────────────────────────────────────────────────────────────────────
# OpenAIRerankDedicated — batched /v1/rerank
# ─────────────────────────────────────────────────────────────────────


def _rerank_body(*idx_score_pairs: tuple[int, float]) -> str:
    """JSON body shaped like vLLM's /v1/rerank response."""
    import json
    return json.dumps({
        "results": [
            {"index": i, "relevance_score": s} for i, s in idx_score_pairs
        ],
    })


def test_openai_rerank_dedicated_unavailable_on_empty_base_url():
    from core.src.query.reranker import OpenAIRerankDedicated
    r = OpenAIRerankDedicated(model_name="m", base_url="")
    assert r.available is False
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:1", "req:2"]


def test_openai_rerank_dedicated_reorders_by_server_ranking(monkeypatch):
    from core.src.query.reranker import OpenAIRerankDedicated
    r = OpenAIRerankDedicated(model_name="m", base_url="http://h")
    # Server returns: doc at index 2 wins, then 0, then 1.
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _rerank_body((2, 0.9), (0, 0.6), (1, 0.2))),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:c", "req:a", "req:b"]


def test_openai_rerank_dedicated_appends_unranked_chunks(monkeypatch):
    """If the server returns fewer than N results, the unranked chunks
    must still appear at the tail (size invariant)."""
    from core.src.query.reranker import OpenAIRerankDedicated
    r = OpenAIRerankDedicated(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _rerank_body((1, 0.9))),  # only ranked one out of three
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert out[0].chunk_id == "req:b"  # the ranked one wins
    assert set(c.chunk_id for c in out) == {"req:a", "req:b", "req:c"}
    assert len(out) == 3


def test_openai_rerank_dedicated_passthrough_on_http_failure(monkeypatch):
    from core.src.query.reranker import OpenAIRerankDedicated
    import urllib.error
    r = OpenAIRerankDedicated(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        urllib.error.URLError("connection refused"),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_openai_rerank_dedicated_passthrough_on_malformed_response(monkeypatch):
    from core.src.query.reranker import OpenAIRerankDedicated
    r = OpenAIRerankDedicated(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, '{"results": "not a list"}'),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_openai_rerank_dedicated_drops_out_of_range_indices(monkeypatch):
    """Defensive: if the server returns an index outside [0, N) the
    reranker must ignore it rather than IndexError."""
    from core.src.query.reranker import OpenAIRerankDedicated
    r = OpenAIRerankDedicated(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _rerank_body((99, 0.9), (1, 0.7), (-1, 0.5))),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    # only idx=1 is valid; req:a appended after as unranked
    assert [c.chunk_id for c in out] == ["req:b", "req:a"]


def test_openai_rerank_dedicated_satisfies_reranker_protocol():
    from core.src.query.reranker import OpenAIRerankDedicated, Reranker
    assert isinstance(
        OpenAIRerankDedicated(model_name="m", base_url="http://h"), Reranker,
    )


# ─────────────────────────────────────────────────────────────────────
# TEIReranker — Cohere-shape /rerank (no /v1 prefix, flat array body)
# ─────────────────────────────────────────────────────────────────────


def _tei_body(*idx_score_pairs: tuple[int, float]) -> str:
    """JSON body shaped like TEI's /rerank response — flat array,
    `score` field (not `relevance_score`), no `results` wrapper."""
    import json
    return json.dumps([
        {"index": i, "score": s} for i, s in idx_score_pairs
    ])


def test_tei_reranker_unavailable_on_empty_base_url():
    from core.src.query.reranker import TEIReranker
    r = TEIReranker(model_name="bge-reranker-large", base_url="")
    assert r.available is False
    chunks = [_chunk("req:1"), _chunk("req:2")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:1", "req:2"]


def test_tei_reranker_reorders_by_server_ranking(monkeypatch):
    from core.src.query.reranker import TEIReranker
    r = TEIReranker(model_name="bge-reranker-large", base_url="http://h")
    # Server ranks index 2 first, then 0, then 1.
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _tei_body((2, 0.92), (0, 0.61), (1, 0.18))),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:c", "req:a", "req:b"]


def test_tei_reranker_appends_unranked_chunks(monkeypatch):
    """If TEI returns fewer rows than chunks (shouldn't happen but be
    defensive), missing chunks land at the tail in input order."""
    from core.src.query.reranker import TEIReranker
    r = TEIReranker(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _tei_body((1, 0.9))),  # only ranks one of three
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert out[0].chunk_id == "req:b"
    assert set(c.chunk_id for c in out) == {"req:a", "req:b", "req:c"}
    assert len(out) == 3


def test_tei_reranker_accepts_legacy_results_wrapper(monkeypatch):
    """The class accepts either a flat array (native TEI) or a
    `{"results": [...]}` wrapper (some forks/proxies). The docstring
    documents both — pin the wrapped case here so a future cleanup
    doesn't accidentally drop the compatibility branch."""
    from core.src.query.reranker import TEIReranker
    import json
    r = TEIReranker(model_name="m", base_url="http://h")
    # Server returns results in ranked order — index 1 first means
    # "req:b ranks first." Same convention as the flat-array case +
    # OpenAIRerankDedicated; the score field is informational.
    wrapped = json.dumps({
        "results": [
            {"index": 1, "score": 0.9},
            {"index": 0, "score": 0.4},
        ],
    })
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [(200, wrapped)])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:b", "req:a"]


def test_tei_reranker_passthrough_on_http_failure(monkeypatch):
    from core.src.query.reranker import TEIReranker
    import urllib.error
    r = TEIReranker(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        urllib.error.URLError("connection refused"),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_tei_reranker_passthrough_on_malformed_response(monkeypatch):
    """Unexpected shapes (neither flat array nor {results: list}) =
    passthrough rather than crash."""
    from core.src.query.reranker import TEIReranker
    r = TEIReranker(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, '{"unexpected": "shape"}'),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:a", "req:b"]


def test_tei_reranker_drops_out_of_range_indices(monkeypatch):
    from core.src.query.reranker import TEIReranker
    r = TEIReranker(model_name="m", base_url="http://h")
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _tei_body((99, 0.9), (1, 0.7), (-1, 0.5))),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    # only idx=1 is valid; req:a appended after as unranked
    assert [c.chunk_id for c in out] == ["req:b", "req:a"]


def test_tei_reranker_sends_query_and_texts_payload_shape(monkeypatch):
    """Pin the TEI wire shape: body uses `query` + `texts` (not
    `documents`/`messages`); URL is `/rerank` (no `/v1` prefix)."""
    from core.src.query.reranker import TEIReranker
    import json
    r = TEIReranker(model_name="m", base_url="http://h")
    sent = _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, _tei_body((0, 0.5))),
    ])
    r.rerank("what is X", [_chunk("req:1", text="Doc A"), _chunk("req:2", text="Doc B")])
    assert len(sent) == 1
    body = sent[0]
    assert body["query"] == "what is X"
    assert body["texts"] == ["Doc A", "Doc B"]
    assert "documents" not in body  # explicitly NOT the openai-rerank-dedicated shape
    assert "model" not in body      # TEI doesn't accept a model field per request


def test_tei_reranker_sends_bearer_token_when_set(monkeypatch):
    from core.src.query.reranker import TEIReranker
    captured: dict = {}

    class _FakeResp:
        status = 200
        def read(self): return b'[{"index": 0, "score": 0.5}]'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _grab(req, timeout=None):
        captured.update(req.headers)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _grab)

    r = TEIReranker(model_name="m", base_url="http://h", api_key="sk-xyz")
    r.rerank("q", [_chunk("req:a")])
    auth = next((v for k, v in captured.items() if k.lower() == "authorization"), None)
    assert auth == "Bearer sk-xyz"


def test_tei_reranker_satisfies_reranker_protocol():
    from core.src.query.reranker import TEIReranker, Reranker
    assert isinstance(
        TEIReranker(model_name="m", base_url="http://h"), Reranker,
    )


# ─────────────────────────────────────────────────────────────────────
# env resolvers + _resolve_reranker dispatch
# ─────────────────────────────────────────────────────────────────────


def test_resolve_reranker_base_url_precedence(monkeypatch):
    from core.src.env.config import (
        RERANKER_BASE_URL_ENV_VAR, resolve_reranker_base_url,
    )
    # env var wins over config_store_value
    monkeypatch.setenv(RERANKER_BASE_URL_ENV_VAR, "http://from-env:8000")
    assert resolve_reranker_base_url(config_store_value="http://from-db") == \
        "http://from-env:8000"
    monkeypatch.delenv(RERANKER_BASE_URL_ENV_VAR, raising=False)
    # config_store_value second
    assert resolve_reranker_base_url(config_store_value="http://from-db") == \
        "http://from-db"
    # default empty
    assert resolve_reranker_base_url(config_store_value=None) == ""


def test_resolve_reranker_api_key_precedence(monkeypatch):
    from core.src.env.config import (
        RERANKER_API_KEY_ENV_VAR, resolve_reranker_api_key,
    )
    monkeypatch.setenv(RERANKER_API_KEY_ENV_VAR, "sk-from-env")
    assert resolve_reranker_api_key(config_store_value="sk-from-db") == \
        "sk-from-env"
    monkeypatch.delenv(RERANKER_API_KEY_ENV_VAR, raising=False)
    assert resolve_reranker_api_key(config_store_value="sk-from-db") == \
        "sk-from-db"
    assert resolve_reranker_api_key(config_store_value=None) == ""


def test_resolve_reranker_provider_accepts_new_options(monkeypatch):
    from core.src.env.config import (
        RERANKER_PROVIDER_ENV_VAR, resolve_reranker_provider,
    )
    for value in ("openai-rerank-chat", "openai-rerank-dedicated", "tei"):
        monkeypatch.setenv(RERANKER_PROVIDER_ENV_VAR, value)
        assert resolve_reranker_provider() == value
    monkeypatch.delenv(RERANKER_PROVIDER_ENV_VAR, raising=False)


def test_resolve_reranker_provider_rejects_unknown_value(monkeypatch):
    """Unknown providers fall through to the default — must not return
    a string the dispatcher doesn't know what to do with."""
    from core.src.env.config import (
        RERANKER_PROVIDER_ENV_VAR, resolve_reranker_provider,
        DEFAULT_RERANKER_PROVIDER,
    )
    monkeypatch.setenv(RERANKER_PROVIDER_ENV_VAR, "bogus-provider")
    assert resolve_reranker_provider() == DEFAULT_RERANKER_PROVIDER
    monkeypatch.delenv(RERANKER_PROVIDER_ENV_VAR, raising=False)


# ─────────────────────────────────────────────────────────────────────
# OpenAIRerankChat batch mode + batch-size resolution
# ─────────────────────────────────────────────────────────────────────


def _batch_response(*id_score_pairs: tuple[int, int]) -> tuple[int, str]:
    """A 200 response whose chat content is the batch JSON array."""
    import json
    arr = [{"id": i, "score": s} for i, s in id_score_pairs]
    return (200, json.dumps({
        "choices": [{"message": {"content": json.dumps(arr)}}],
    }))


def test_openai_rerank_chat_batch_mode_uses_single_call_per_batch(monkeypatch):
    """batch_size=N collapses N chunks into ONE HTTP request."""
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=3,
    )
    sent = _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _batch_response((0, 30), (1, 90), (2, 60)),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    # Three chunks, batch_size=3 → exactly one HTTP call.
    assert len(sent) == 1
    # Sorted by score desc: b (90) > c (60) > a (30).
    assert [c.chunk_id for c in out] == ["req:b", "req:c", "req:a"]


def test_openai_rerank_chat_batch_mode_packs_multiple_batches(monkeypatch):
    """batch_size=2 over 5 chunks → 3 batches (2+2+1)."""
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=2,
    )
    sent = _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _batch_response((0, 20), (1, 80)),  # batch 1: a, b
        _batch_response((0, 60), (1, 40)),  # batch 2: c, d
        _batch_response((0, 90)),           # batch 3: e
    ])
    chunks = [_chunk(f"req:{x}") for x in "abcde"]
    out = r.rerank("q", chunks)
    assert len(sent) == 3
    # Scores: a=20, b=80, c=60, d=40, e=90. Sorted desc: e, b, c, d, a.
    assert [c.chunk_id for c in out] == ["req:e", "req:b", "req:c", "req:d", "req:a"]


def test_openai_rerank_chat_batch_failure_zeros_whole_batch(monkeypatch):
    """One bad LLM call zeros every chunk in that batch (D-089 invariant).
    Other batches are unaffected."""
    from core.src.query.reranker import OpenAIRerankChat
    import urllib.error
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=2,
    )
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _batch_response((0, 70), (1, 50)),                    # ok: a=70, b=50
        urllib.error.URLError("connection refused"),          # batch failure: c=0, d=0
        _batch_response((0, 90)),                             # ok: e=90
    ])
    chunks = [_chunk(f"req:{x}") for x in "abcde"]
    out = r.rerank("q", chunks)
    # Scores: a=70, b=50, c=0, d=0, e=90.
    # Sorted: e(90) > a(70) > b(50) > c(0)=d(0) → tiebreak by input order.
    assert [c.chunk_id for c in out] == ["req:e", "req:a", "req:b", "req:c", "req:d"]


def test_openai_rerank_chat_batch_size_larger_than_n_collapses_to_one(monkeypatch):
    """batch_size=100 with 3 chunks → still just one HTTP call."""
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=100,
    )
    sent = _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _batch_response((0, 40), (1, 60), (2, 50)),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b"), _chunk("req:c")]
    out = r.rerank("q", chunks)
    assert len(sent) == 1
    assert [c.chunk_id for c in out] == ["req:b", "req:c", "req:a"]


def test_openai_rerank_chat_batch_parse_tolerates_cot_preamble(monkeypatch):
    """Reasoning models often emit '<think>...</think>' or 'Let me think...'
    before the JSON array — parser must scan past it."""
    from core.src.query.reranker import OpenAIRerankChat
    import json
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=2,
    )
    noisy = (
        "Let me think about each document...\n"
        "Document 0 talks about something else.\n"
        "Document 1 looks more relevant.\n"
        "Here's the JSON:\n"
        + json.dumps([{"id": 0, "score": 10}, {"id": 1, "score": 85}])
        + "\nDone."
    )
    _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        (200, json.dumps({"choices": [{"message": {"content": noisy}}]})),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    assert [c.chunk_id for c in out] == ["req:b", "req:a"]


def test_openai_rerank_chat_batch_size_1_uses_per_call_path(monkeypatch):
    """batch_size=1 must NOT enter batch mode — must keep per-call
    semantics (1 HTTP request per chunk)."""
    from core.src.query.reranker import OpenAIRerankChat
    r = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=1,
    )
    sent = _stub_urlopen(monkeypatch, "core.src.query.reranker", [
        _ok(60),  # per-call response shape (not batch JSON)
        _ok(80),
    ])
    chunks = [_chunk("req:a"), _chunk("req:b")]
    out = r.rerank("q", chunks)
    # 2 chunks, batch_size=1 → 2 HTTP calls (per-call path).
    assert len(sent) == 2
    assert [c.chunk_id for c in out] == ["req:b", "req:a"]


def test_openai_rerank_chat_batch_size_zero_or_negative_clamped_to_one():
    """batch_size <= 1 always means per-call. Pinned so an env-var typo
    or config bug can't blow up."""
    from core.src.query.reranker import OpenAIRerankChat
    r0 = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=0,
    )
    assert r0._batch_size == 1
    rneg = OpenAIRerankChat(
        model_name="m", base_url="http://h", batch_size=-5,
    )
    assert rneg._batch_size == 1


# ─────────────────────────────────────────────────────────────────────
# resolve_reranker_batch_size + env back-compat
# ─────────────────────────────────────────────────────────────────────


def test_resolve_reranker_batch_size_default_is_one(monkeypatch):
    from core.src.env.config import (
        RERANKER_BATCH_SIZE_ENV_VAR,
        RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED,
        resolve_reranker_batch_size,
        DEFAULT_RERANKER_BATCH_SIZE,
    )
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR, raising=False)
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED, raising=False)
    assert resolve_reranker_batch_size() == DEFAULT_RERANKER_BATCH_SIZE
    assert DEFAULT_RERANKER_BATCH_SIZE == 1


def test_resolve_reranker_batch_size_prefers_new_env_over_deprecated(monkeypatch):
    """When both NORA_RERANK_BATCH_SIZE and the deprecated alias are
    set, the new name wins."""
    from core.src.env.config import (
        RERANKER_BATCH_SIZE_ENV_VAR,
        RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED,
        resolve_reranker_batch_size,
    )
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR, "10")
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED, "25")
    assert resolve_reranker_batch_size() == 10


def test_resolve_reranker_batch_size_honors_deprecated_with_warning(monkeypatch, caplog):
    """If only the deprecated env var is set, it's honored AND a
    deprecation warning is logged."""
    from core.src.env.config import (
        RERANKER_BATCH_SIZE_ENV_VAR,
        RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED,
        resolve_reranker_batch_size,
    )
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR, raising=False)
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED, "8")
    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        assert resolve_reranker_batch_size() == 8
    assert any(
        "NORA_SIRA_RERANK_BATCH_SIZE is deprecated" in rec.message
        for rec in caplog.records
    )


def test_resolve_reranker_batch_size_clamps_below_one(monkeypatch):
    """Zero and negative values collapse to 1 (per-call). Bad env input
    falls through to the next source rather than crashing."""
    from core.src.env.config import (
        RERANKER_BATCH_SIZE_ENV_VAR,
        RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED,
        resolve_reranker_batch_size,
    )
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED, raising=False)
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR, "0")
    assert resolve_reranker_batch_size() == 1
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR, "-5")
    assert resolve_reranker_batch_size() == 1
    monkeypatch.setenv(RERANKER_BATCH_SIZE_ENV_VAR, "not-a-number")
    # bad env → fall through to default
    assert resolve_reranker_batch_size() == 1


def test_resolve_reranker_batch_size_db_then_default(monkeypatch):
    from core.src.env.config import (
        RERANKER_BATCH_SIZE_ENV_VAR,
        RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED,
        resolve_reranker_batch_size,
    )
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR, raising=False)
    monkeypatch.delenv(RERANKER_BATCH_SIZE_ENV_VAR_DEPRECATED, raising=False)
    assert resolve_reranker_batch_size(config_store_value="7") == 7
    assert resolve_reranker_batch_size(config_store_value="bogus") == 1
