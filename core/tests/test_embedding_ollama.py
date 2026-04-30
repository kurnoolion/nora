"""Tests for OllamaEmbedder + make_embedder factory."""

from __future__ import annotations

import json
import urllib.request
from unittest.mock import MagicMock, patch

import pytest

from core.src.vectorstore.config import VectorStoreConfig
from core.src.vectorstore.embedding_base import EmbeddingProvider
from core.src.vectorstore.embedding_ollama import (
    OllamaEmbedder,
    _build_opener,
    _l2_normalize,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_opener(responses: list[bytes]) -> MagicMock:
    """Build a mock opener that returns successive bytes payloads on .open()."""
    opener = MagicMock(spec=urllib.request.OpenerDirector)
    iter_responses = iter(responses)
    opener.open.side_effect = lambda req, timeout=None: _MockResponse(next(iter_responses))
    return opener


def _tags_payload(models: list[str]) -> bytes:
    return json.dumps({"models": [{"name": n} for n in models]}).encode("utf-8")


def _emb_payload(vec: list[float]) -> bytes:
    return json.dumps({"embedding": vec}).encode("utf-8")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_l2_normalize_unit_vector():
    assert _l2_normalize([3.0, 4.0]) == [0.6, 0.8]


def test_l2_normalize_zero_vector_returns_input():
    assert _l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_proxy_bypass_for_loopback_host():
    """_build_opener installs an empty ProxyHandler for localhost-class hosts."""
    for url in (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
    ):
        with patch(
            "core.src.vectorstore.embedding_ollama.urllib.request.build_opener"
        ) as mock_build:
            mock_build.return_value = MagicMock(spec=urllib.request.OpenerDirector)
            _build_opener(url)
            assert mock_build.call_count == 1
            args, _ = mock_build.call_args
            assert len(args) == 1, f"expected 1 handler arg for {url}, got {args}"
            handler = args[0]
            assert isinstance(handler, urllib.request.ProxyHandler)
            assert handler.proxies == {}, f"expected empty proxies for {url}, got {handler.proxies}"


def test_proxy_not_bypassed_for_remote_host():
    """For non-loopback hosts, build_opener is called with no handler args (system proxies apply)."""
    with patch(
        "core.src.vectorstore.embedding_ollama.urllib.request.build_opener"
    ) as mock_build:
        mock_build.return_value = MagicMock(spec=urllib.request.OpenerDirector)
        _build_opener("http://remote.example.com:11434")
        args, _ = mock_build.call_args
        assert args == ()


# ---------------------------------------------------------------------------
# OllamaEmbedder construction + Protocol conformance
# ---------------------------------------------------------------------------


def test_satisfies_embedding_provider_protocol():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([_tags_payload(["nomic-embed-text:latest"])])
        emb = OllamaEmbedder(model_name="nomic-embed-text")
    assert isinstance(emb, EmbeddingProvider)
    assert emb.model_name == "nomic-embed-text"


def test_constructor_warns_when_model_missing(caplog):
    """If the requested model isn't in /api/tags, log a warning (don't fail)."""
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([_tags_payload(["other-model:latest"])])
        with caplog.at_level("WARNING", logger="core.src.vectorstore.embedding_ollama"):
            emb = OllamaEmbedder(model_name="nomic-embed-text")
    assert emb.model_name == "nomic-embed-text"
    assert any("not found on Ollama server" in r.message for r in caplog.records)


def test_constructor_raises_when_server_unreachable():
    """ConnectionError on /api/tags ping bubbles up as ConnectionError."""
    failing_opener = MagicMock(spec=urllib.request.OpenerDirector)
    failing_opener.open.side_effect = urllib.error.URLError("connection refused")
    with patch("core.src.vectorstore.embedding_ollama._build_opener", return_value=failing_opener):
        with pytest.raises(ConnectionError, match="Cannot connect to Ollama"):
            OllamaEmbedder(model_name="nomic-embed-text")


# ---------------------------------------------------------------------------
# embed() / embed_query() behavior
# ---------------------------------------------------------------------------


def test_embed_returns_normalized_vectors_by_default():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([3.0, 4.0]),  # magnitude 5 -> normalized to (0.6, 0.8)
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text", normalize=True)
        vecs = emb.embed(["hello"])
    assert vecs == [[0.6, 0.8]]


def test_embed_passes_through_when_normalize_false():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([3.0, 4.0]),
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text", normalize=False)
        vecs = emb.embed(["hello"])
    assert vecs == [[3.0, 4.0]]


def test_embed_handles_batch_with_one_call_per_text():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([1.0, 0.0]),
            _emb_payload([0.0, 1.0]),
            _emb_payload([1.0, 1.0]),  # magnitude sqrt(2) -> normalized
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text", normalize=True)
        vecs = emb.embed(["a", "b", "c"])
    assert len(vecs) == 3
    assert vecs[0] == [1.0, 0.0]
    assert vecs[1] == [0.0, 1.0]
    # Third vector normalized
    assert vecs[2][0] == pytest.approx(0.7071, abs=1e-3)


def test_embed_query_delegates_to_embed():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([1.0, 0.0]),
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text", normalize=False)
    assert emb.embed_query("query text") == [1.0, 0.0]


def test_empty_input_returns_empty_without_calling_api():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        # Only the /api/tags call is expected.
        mock_builder.return_value = _make_opener([_tags_payload(["nomic-embed-text:latest"])])
        emb = OllamaEmbedder(model_name="nomic-embed-text")
        assert emb.embed([]) == []


def test_empty_embedding_response_raises():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([]),  # server returned no embedding
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text")
        with pytest.raises(RuntimeError, match="empty embedding"):
            emb.embed(["text"])


def test_dimension_recorded_from_first_call():
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["nomic-embed-text:latest"]),
            _emb_payload([0.1] * 768),
        ])
        emb = OllamaEmbedder(model_name="nomic-embed-text", normalize=False)
        emb.embed(["test"])
    assert emb.dimension == 768


def test_oversize_text_truncated_at_max_input_chars(caplog):
    """Texts longer than max_input_chars are truncated (with a warning) so the
    embedder doesn't trip a model context-window 500."""
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([
            _tags_payload(["qwen3-embedding-q8-0:4b:latest"]),
            _emb_payload([1.0, 0.0]),
        ])
        emb = OllamaEmbedder(
            model_name="qwen3-embedding-q8-0:4b",
            normalize=False,
            max_input_chars=100,
        )
        with caplog.at_level("WARNING", logger="core.src.vectorstore.embedding_ollama"):
            emb.embed(["x" * 250])
    assert emb._truncated_count == 1
    assert any("truncating" in r.message for r in caplog.records)


def test_make_embedder_passes_ollama_max_input_chars_from_extra():
    config = VectorStoreConfig(
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        extra={"ollama_max_input_chars": 4096},
    )
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([_tags_payload(["nomic-embed-text:latest"])])
        from core.src.vectorstore import make_embedder
        emb = make_embedder(config)
    assert emb._max_input_chars == 4096


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------


def test_make_embedder_routes_ollama():
    config = VectorStoreConfig(
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
    )
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([_tags_payload(["nomic-embed-text:latest"])])
        from core.src.vectorstore import make_embedder
        emb = make_embedder(config)
    assert isinstance(emb, OllamaEmbedder)
    assert emb.model_name == "nomic-embed-text"


def test_make_embedder_passes_ollama_url_from_extra():
    config = VectorStoreConfig(
        embedding_provider="ollama",
        embedding_model="nomic-embed-text",
        extra={"ollama_url": "http://192.168.1.10:11434", "ollama_timeout_s": 120},
    )
    with patch("core.src.vectorstore.embedding_ollama._build_opener") as mock_builder:
        mock_builder.return_value = _make_opener([_tags_payload(["nomic-embed-text:latest"])])
        from core.src.vectorstore import make_embedder
        emb = make_embedder(config)
    assert emb._base_url == "http://192.168.1.10:11434"
    assert emb._timeout == 120


def test_make_embedder_unknown_provider_raises():
    config = VectorStoreConfig(embedding_provider="bogus")
    from core.src.vectorstore import make_embedder
    with pytest.raises(ValueError, match="Unknown embedding_provider"):
        make_embedder(config)


def test_make_embedder_alias_st_for_sentence_transformers():
    """The factory accepts 'st' as a shortcut alias to ensure dispatch is robust."""
    config = VectorStoreConfig(embedding_provider="st")
    # We don't actually want to load sentence-transformers in this test, just
    # verify the factory routes to it (which will fail on a missing dep on
    # some CI machines). Use importorskip to gate.
    pytest.importorskip("sentence_transformers")
    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.embedding_st import SentenceTransformerEmbedder
    emb = make_embedder(config)
    assert isinstance(emb, SentenceTransformerEmbedder)


def test_make_embedder_alias_huggingface_for_sentence_transformers():
    """'huggingface' (and 'hf') route to SentenceTransformerEmbedder."""
    pytest.importorskip("sentence_transformers")
    from core.src.vectorstore import make_embedder
    from core.src.vectorstore.embedding_st import SentenceTransformerEmbedder

    for alias in ("huggingface", "hf"):
        config = VectorStoreConfig(embedding_provider=alias)
        emb = make_embedder(config)
        assert isinstance(emb, SentenceTransformerEmbedder), f"alias {alias!r} did not route"
