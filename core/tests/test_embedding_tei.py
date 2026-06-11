"""Tests for TEIEmbedder + make_embedder factory wiring."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from core.src.vectorstore.config import VectorStoreConfig
from core.src.vectorstore.embedding_base import EmbeddingProvider
from core.src.vectorstore.embedding_tei import (
    TEIEmbedder,
    _DEFAULT_MAX_INPUT_CHARS,
    _l2_normalize,
)


# ---------------------------------------------------------------------------
# urllib stub — mirrors the test_query_reranker.py pattern
# ---------------------------------------------------------------------------


def _stub_urlopen(monkeypatch, responses):
    """Patch urllib.request.urlopen so each call returns the next item in
    `responses` — either (status, body_str) or an Exception. Returns the
    list of captured request payloads (one dict per call)."""
    sent: list[dict] = []
    it = iter(responses)

    class _FakeResp:
        def __init__(self, status, body):
            self.status = status
            self._body = body
        def read(self):
            return self._body.encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        try:
            r = next(it)
        except StopIteration:
            r = (200, json.dumps([[0.0]]))
        if isinstance(r, Exception):
            raise r
        status, body = r
        return _FakeResp(status, body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    return sent


def _vecs(*vectors: list[float]) -> str:
    """JSON body for TEI native /embed response — array of arrays."""
    return json.dumps(list(vectors))


# ---------------------------------------------------------------------------
# Init behavior
# ---------------------------------------------------------------------------


def test_tei_embedder_requires_base_url():
    with pytest.raises(ValueError, match="base_url is required"):
        TEIEmbedder(model_name="m", base_url="")


def test_tei_embedder_init_probe_captures_dimension(monkeypatch):
    """Init runs a one-shot probe to verify reachability and learn dim."""
    _stub_urlopen(monkeypatch, [(200, _vecs([0.1] * 1024))])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    assert r.dimension == 1024


def test_tei_embedder_init_probe_failure_raises_connection_error(monkeypatch):
    _stub_urlopen(monkeypatch, [urllib.error.URLError("connection refused")])
    with pytest.raises(ConnectionError, match="cannot reach"):
        TEIEmbedder(model_name="m", base_url="http://h")


def test_tei_embedder_satisfies_embedding_provider_protocol(monkeypatch):
    _stub_urlopen(monkeypatch, [(200, _vecs([0.0] * 8))])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    assert isinstance(r, EmbeddingProvider)


# ---------------------------------------------------------------------------
# Embed behavior
# ---------------------------------------------------------------------------


def test_tei_embedder_returns_one_vector_per_input(monkeypatch):
    # Init probe + one batch call
    sent = _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0, 0.0])),                       # init
        (200, _vecs([1.0, 0.0], [0.0, 1.0], [1.0, 1.0])),
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h", normalize=False)
    out = r.embed(["a", "b", "c"])
    assert len(out) == 3
    assert out[0] == [1.0, 0.0]
    # The init payload is sent[0]; the embed call is sent[1].
    assert sent[1]["inputs"] == ["a", "b", "c"]
    assert sent[1]["truncate"] is True


def test_tei_embedder_normalizes_when_requested(monkeypatch):
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                # init
        (200, _vecs([3.0, 4.0])),           # 3-4-5 triangle
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h", normalize=True)
    out = r.embed(["x"])
    # 3-4-5 → normalized to (0.6, 0.8)
    assert out[0][0] == pytest.approx(0.6, abs=1e-6)
    assert out[0][1] == pytest.approx(0.8, abs=1e-6)


def test_tei_embedder_empty_input_returns_empty_no_http(monkeypatch):
    # Only the init probe should fire.
    sent = _stub_urlopen(monkeypatch, [(200, _vecs([0.0]))])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    out = r.embed([])
    assert out == []
    assert len(sent) == 1  # only init


def test_tei_embedder_splits_large_input_at_max_batch_size(monkeypatch):
    """5 inputs with max_batch_size=2 → 3 HTTP calls after init."""
    sent = _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                            # init
        (200, _vecs([1.0], [1.0])),                     # batch 1 — texts 0, 1
        (200, _vecs([2.0], [2.0])),                     # batch 2 — texts 2, 3
        (200, _vecs([3.0])),                            # batch 3 — text 4
    ])
    r = TEIEmbedder(
        model_name="m", base_url="http://h",
        max_batch_size=2, normalize=False,
    )
    out = r.embed([f"t{i}" for i in range(5)])
    assert len(out) == 5
    # 1 init + 3 embed calls = 4 calls total
    assert len(sent) == 4
    assert sent[1]["inputs"] == ["t0", "t1"]
    assert sent[2]["inputs"] == ["t2", "t3"]
    assert sent[3]["inputs"] == ["t4"]


def test_tei_embedder_truncates_long_texts_before_post(monkeypatch):
    sent = _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                # init
        (200, _vecs([0.0])),                # one embed call
    ])
    r = TEIEmbedder(
        model_name="m", base_url="http://h",
        max_input_chars=100, normalize=False,
    )
    long_text = "a" * 5000
    r.embed([long_text])
    assert len(sent[1]["inputs"][0]) == 100


def test_tei_embedder_embed_query_delegates_to_embed(monkeypatch):
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                # init
        (200, _vecs([0.5, 0.5])),
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h", normalize=False)
    v = r.embed_query("hi")
    assert v == [0.5, 0.5]


def test_tei_embedder_accepts_wrapped_embeddings_response(monkeypatch):
    """Some TEI forks wrap the response in {'embeddings': [...]}."""
    wrapped = json.dumps({"embeddings": [[1.0, 2.0]]})
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                # init
        (200, wrapped),
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h", normalize=False)
    out = r.embed(["x"])
    assert out == [[1.0, 2.0]]


def test_tei_embedder_bearer_token_set_when_api_key_provided(monkeypatch):
    """API key is forwarded as Authorization: Bearer ..."""
    captured: dict = {}

    class _FakeResp:
        status = 200
        def read(self): return _vecs([0.0]).encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _grab(req, timeout=None):
        captured.update(req.headers)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _grab)

    TEIEmbedder(model_name="m", base_url="http://h", api_key="sk-xyz")
    auth = next((v for k, v in captured.items() if k.lower() == "authorization"), None)
    assert auth == "Bearer sk-xyz"


def test_tei_embedder_raises_on_unexpected_response_shape(monkeypatch):
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                            # init
        (200, json.dumps({"unexpected": "shape"})),     # bad shape
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    with pytest.raises(RuntimeError, match="unexpected response shape"):
        r.embed(["x"])


def test_tei_embedder_raises_on_http_error_with_body_in_message(monkeypatch):
    err = urllib.error.HTTPError(
        url="http://h/embed", code=422, msg="Unprocessable",
        hdrs=None, fp=None,
    )
    # Stub HTTPError.read so the error body can be captured.
    err.read = lambda: b'{"error":"oversized"}'  # type: ignore[method-assign]
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                # init
        err,                                 # embed call
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    with pytest.raises(urllib.error.HTTPError, match="oversized"):
        r.embed(["x"])


def test_tei_embedder_protocol_violation_returns_wrong_count(monkeypatch):
    """If TEI returns N≠M vectors for M inputs, raise — protocol mismatch."""
    _stub_urlopen(monkeypatch, [
        (200, _vecs([0.0])),                            # init
        (200, _vecs([1.0, 2.0])),                       # only 1 vec for 2 inputs
    ])
    r = TEIEmbedder(model_name="m", base_url="http://h")
    with pytest.raises(RuntimeError, match="protocol mismatch"):
        r.embed(["a", "b"])


# ---------------------------------------------------------------------------
# make_embedder factory wiring
# ---------------------------------------------------------------------------


def test_make_embedder_tei_requires_base_url(monkeypatch):
    """Without NORA_EMBEDDING_BASE_URL or extra['tei_base_url'], raise."""
    monkeypatch.delenv("NORA_EMBEDDING_BASE_URL", raising=False)
    from core.src.vectorstore import make_embedder
    cfg = VectorStoreConfig(embedding_provider="tei", embedding_model="m")
    with pytest.raises(ValueError, match="requires a base URL"):
        make_embedder(cfg)


def test_make_embedder_tei_reads_base_url_from_extras(monkeypatch):
    """extras['tei_base_url'] is honored when env var is unset."""
    monkeypatch.delenv("NORA_EMBEDDING_BASE_URL", raising=False)
    _stub_urlopen(monkeypatch, [(200, _vecs([0.0] * 4))])
    from core.src.vectorstore import make_embedder
    cfg = VectorStoreConfig(
        embedding_provider="tei", embedding_model="m",
        extra={"tei_base_url": "http://h"},
    )
    r = make_embedder(cfg)
    assert r.dimension == 4


def test_make_embedder_tei_env_var_overrides_extras(monkeypatch):
    """NORA_EMBEDDING_BASE_URL wins over extras['tei_base_url']."""
    monkeypatch.setenv("NORA_EMBEDDING_BASE_URL", "http://env-wins")
    captured_url: list[str] = []

    class _FakeResp:
        status = 200
        def read(self): return _vecs([0.0] * 2).encode("utf-8")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _grab(req, timeout=None):
        captured_url.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr("urllib.request.urlopen", _grab)
    from core.src.vectorstore import make_embedder
    cfg = VectorStoreConfig(
        embedding_provider="tei", embedding_model="m",
        extra={"tei_base_url": "http://extras-loses"},
    )
    make_embedder(cfg)
    assert captured_url[0].startswith("http://env-wins/embed")


def test_embedding_providers_includes_tei():
    from core.src.env.config import EMBEDDING_PROVIDERS
    assert "tei" in EMBEDDING_PROVIDERS


def test_resolve_embedding_base_url_env_var(monkeypatch):
    from core.src.env.config import resolve_embedding_base_url
    monkeypatch.setenv("NORA_EMBEDDING_BASE_URL", "http://from-env")
    assert resolve_embedding_base_url(config_store_value="http://from-db") == \
        "http://from-env"
    monkeypatch.delenv("NORA_EMBEDDING_BASE_URL", raising=False)
    assert resolve_embedding_base_url(config_store_value="http://from-db") == \
        "http://from-db"
    assert resolve_embedding_base_url() == ""


def test_resolve_embedding_api_key_env_var(monkeypatch):
    from core.src.env.config import resolve_embedding_api_key
    monkeypatch.setenv("NORA_EMBEDDING_API_KEY", "sk-env")
    assert resolve_embedding_api_key(config_store_value="sk-db") == "sk-env"
    monkeypatch.delenv("NORA_EMBEDDING_API_KEY", raising=False)
    assert resolve_embedding_api_key(config_store_value="sk-db") == "sk-db"
    assert resolve_embedding_api_key() == ""
